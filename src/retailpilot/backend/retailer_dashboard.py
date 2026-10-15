"""RetailPilot retailer dashboard.

Reads the data scraped by amazon_ecomm_crawler (MongoDB) and gives a retailer:

  - Current prices, last-days trend, and competitor prices in the selected city
  - A pricing-gap heatmap (price vs. the market median, by city)
  - Margin scenarios from the retailer's own landed cost (match / hold / raise)
  - Review sentiment: a TF-IDF + logistic regression model trained on star ratings
  - Market basket analysis (research): pair rules from co-reviewing customers

Data is handled with Polars. scikit-learn is used only for the sentiment model.

Run from the repo root (the crawler must have written data first):

    streamlit run src/retailpilot/retailer_dashboard/dashboard.py

Environment:
    MONGO_URI    default mongodb://localhost:27018 (the crawler's docker-compose port)
    MONGO_DB     default amazon_ecomm
    STALE_HOURS  default 48; older observations are flagged as stale
"""

import math
import os
from collections import Counter
from datetime import UTC, datetime, timedelta
from itertools import combinations

import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import polars as pl
import streamlit as st
from pymongo import MongoClient
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score
from sklearn.pipeline import make_pipeline

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27018")
MONGO_DB = os.getenv("MONGO_DB", "amazon_ecomm")
STALE_HOURS = float(os.getenv("STALE_HOURS", "48"))

MAX_HISTORY_DAYS = 90
PEER_COUNT = 12
THIN_MARGIN_PCT = 10.0
RAISE_PCT = 5.0
DEFAULT_GST_PCT = 18.0  # GST on laptops in India; editable in the margin tab
POS_T, NEG_T = 0.65, 0.35  # model probability cut-offs for Positive / Negative
GENERIC_AUTHORS = ["", "amazon customer", "customer"]
UTC_DT = pl.Datetime("us", "UTC")

COLORS = {"us": "#1a73e8", "peer": "#9aa0a6", "good": "#1e8e3e", "bad": "#d93025", "warn": "#f29900"}

# Mongo projections, with the Polars dtype each column is cast to after loading.
PRODUCT_FIELDS = {"asin": pl.Utf8, "title": pl.Utf8, "brand": pl.Utf8, "rating": pl.Float64,
                  "rating_count": pl.Float64, "category_names": pl.List(pl.Utf8), "url": pl.Utf8}
OFFER_FIELDS = {"asin": pl.Utf8, "city": pl.Utf8, "city_name": pl.Utf8, "price": pl.Float64,
                "mrp": pl.Float64, "discount_pct": pl.Float64, "availability": pl.Utf8,
                "seller": pl.Utf8, "delivery_date": pl.Utf8,
                "listing_scraped_at": UTC_DT, "detail_scraped_at": UTC_DT}
HISTORY_FIELDS = {"asin": pl.Utf8, "city": pl.Utf8, "price": pl.Float64, "scraped_at": UTC_DT}
REVIEW_FIELDS = {"asin": pl.Utf8, "author": pl.Utf8, "rating": pl.Float64, "title": pl.Utf8, "body": pl.Utf8}

CSS = """
<style>
.hero {background: linear-gradient(120deg, #1a73e8 0%, #0b3d91 100%); color: #fff;
       padding: 1.1rem 1.4rem; border-radius: 14px; margin-bottom: 1rem;}
.hero h1 {color: #fff; margin: 0; font-size: 1.9rem;}
.hero p {margin: .25rem 0 0 0; opacity: .9;}
.note {background: #fff8e1; border-left: 4px solid #f29900; padding: .6rem .9rem;
       border-radius: 8px; font-size: .92rem; margin-bottom: .8rem;}
</style>
"""


# --------------------------------------------------------------------------- helpers

def is_missing(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def subcategory(names) -> str:
    """The most specific browse name: the first crawled name that is not the root 'Electronics'."""
    if isinstance(names, list):
        for name in names:
            if name and name.lower() != "electronics":
                return name
    return "Other"


def short_title(title, asin: str, limit: int = 48) -> str:
    text = title if isinstance(title, str) and title.strip() else asin
    return text if len(text) <= limit else text[: limit - 1] + "…"


def product_url(asin: str, url=None) -> str:
    if isinstance(url, str) and url.startswith("http"):
        return url
    return f"https://www.amazon.in/dp/{asin}"


def is_stale(ts) -> bool:
    return ts is not None and ts < datetime.now(UTC) - timedelta(hours=STALE_HOURS)


# --------------------------------------------------------------------------- data access

def _projection(fields: dict) -> dict:
    return {"_id": 0, **{k: 1 for k in fields}}


def _frame(cursor, fields: dict) -> pl.DataFrame:
    """Turn a Mongo cursor into a DataFrame with every expected column, typed, even when empty."""
    rows = list(cursor)
    df = pl.from_dicts(rows, infer_schema_length=None, strict=False) if rows else pl.DataFrame()
    missing = [c for c in fields if c not in df.columns]
    if missing:
        df = df.with_columns([pl.lit(None).alias(c) for c in missing])
    return df.select([pl.col(c).cast(t, strict=False) for c, t in fields.items()])


@st.cache_data(ttl=600, show_spinner="Reading crawl data from MongoDB…")
def load_data(uri: str, db_name: str, history_days: int) -> dict:
    client = MongoClient(uri, serverSelectionTimeoutMS=3000, tz_aware=True)
    try:
        db = client[db_name]
        since = datetime.now(UTC) - timedelta(days=history_days)
        products = _frame(db.products.find({}, _projection(PRODUCT_FIELDS)), PRODUCT_FIELDS)
        offers = _frame(db.city_offers.find({}, _projection(OFFER_FIELDS)), OFFER_FIELDS)
        history = _frame(
            db.price_history.find({"scraped_at": {"$gte": since}}, _projection(HISTORY_FIELDS)), HISTORY_FIELDS,
        )
        reviews = _frame(db.reviews.find({}, _projection(REVIEW_FIELDS)), REVIEW_FIELDS)
        runs = list(db.crawl_runs.find({}, {"_id": 0, "started_at": 1, "finished_at": 1, "status": 1,
                                            "stats": 1}).sort("started_at", -1).limit(5))
    finally:
        client.close()

    products = products.with_columns(
        category=pl.col("category_names").map_elements(subcategory, return_dtype=pl.Utf8),
    ).drop("category_names")

    offers = offers.with_columns(
        observed_at=pl.max_horizontal([pl.col("detail_scraped_at"), pl.col("listing_scraped_at")]),
    ).join(
        products.select(["asin", "title", "brand", "category", "rating", "rating_count", "url"]),
        on="asin", how="left",
    )

    return {"products": products, "offers": offers, "history": history, "reviews": reviews, "runs": runs}


# --------------------------------------------------------------------------- pricing

def daily_last_price(history: pl.DataFrame, asins: list[str], city: str, days: int) -> pl.DataFrame:
    """Last observed price per product per day, for one city and window."""
    empty = pl.DataFrame(schema={"asin": pl.Utf8, "day": UTC_DT, "price": pl.Float64})
    if history.is_empty() or not asins:
        return empty
    cutoff = datetime.now(UTC) - timedelta(days=days)
    h = history.filter(
        pl.col("city") == city,
        pl.col("asin").is_in(asins),
        pl.col("scraped_at") >= cutoff,
        pl.col("price").is_not_null(),
    )
    if h.is_empty():
        return empty
    return (
        h.sort("scraped_at")
        .with_columns(day=pl.col("scraped_at").dt.truncate("1d"))
        .group_by(["asin", "day"], maintain_order=True)
        .agg(pl.col("price").last())
    )


def peers_of(city_offers: pl.DataFrame, sel: dict, n: int = PEER_COUNT) -> pl.DataFrame:
    """Competing products in the same subcategory and city, closest in price first."""
    return (
        city_offers.filter((pl.col("category") == sel["category"]) & (pl.col("asin") != sel["asin"]))
        .with_columns(gap=(pl.col("price") - sel["price"]).abs())
        .sort("gap")
        .head(n)
    )


def price_gap_matrix(category_offers: pl.DataFrame) -> pl.DataFrame:
    """Percent each city's price sits above (+) or below (-) the product's median price across cities.

    Rows are products (label), columns are cities.
    """
    wide = category_offers.select(["label", "city_name", "price"]).pivot(
        on="city_name", index="label", values="price", aggregate_function="last",
    )
    cities = [c for c in wide.columns if c != "label"]
    if not cities:
        return wide.select("label")
    median = pl.concat_list([pl.col(c) for c in cities]).list.median()
    gap = wide.select("label", *[((pl.col(c) / median) - 1).mul(100).alias(c) for c in cities])
    return gap.filter(pl.any_horizontal([pl.col(c).is_not_null() for c in cities]))


# --------------------------------------------------------------------------- margin

def landed_cost(unit_cost: float, shipping: float, duty_pct: float) -> float:
    return (unit_cost + shipping) * (1 + duty_pct / 100)


def margin_at(price: float, landed: float, gst_pct: float, fees_pct: float) -> dict:
    """Margin at a shelf price that includes GST.

    Net revenue strips GST. Marketplace fees are a share of net revenue.
    contribution = net - landed - fees; breakeven is the shelf price where contribution is 0.
    """
    net = price / (1 + gst_pct / 100)
    fees = net * fees_pct / 100
    contribution = net - landed - fees
    return {
        "net_revenue": net,
        "contribution": contribution,
        "margin_pct": contribution / net * 100 if net else 0.0,
        "breakeven_price": landed / (1 - fees_pct / 100) * (1 + gst_pct / 100),
    }


def margin_scenarios(current: float, lowest_peer: float | None, landed: float, gst_pct: float,
                     fees_pct: float, peer_prices: list[float]) -> pl.DataFrame:
    options = [
        ("Match lowest peer", lowest_peer if lowest_peer else current),
        ("Hold price", current),
        (f"Raise {RAISE_PCT:g}%", current * (1 + RAISE_PCT / 100)),
    ]
    rows = []
    for name, price in options:
        m = margin_at(price, landed, gst_pct, fees_pct)
        if price < m["breakeven_price"]:
            risk = "Below breakeven"
        elif m["margin_pct"] < THIN_MARGIN_PCT:
            risk = f"Thin margin (<{THIN_MARGIN_PCT:g}%)"
        else:
            risk = "OK"
        rows.append({
            "scenario": name,
            "price": price,
            **m,
            "peers_cheaper": sum(p < price for p in peer_prices),
            "risk": risk,
        })
    return pl.DataFrame(rows)


# --------------------------------------------------------------------------- sentiment (ML)

@st.cache_data(show_spinner="Training the sentiment model on review text…")
def build_sentiment(reviews: pl.DataFrame) -> dict:
    """Train on reviews with a clear star rating, then score every review with text.

    Labels are weak: 4-5 stars = positive, 1-2 stars = negative, 3 stars is left out of training.
    Accuracy is 5-fold cross-validation on those labels, so it measures agreement with star ratings,
    not with human judgement.
    """
    r = (
        reviews.filter(pl.col("rating").is_not_null())
        .with_columns(text=(pl.col("title").fill_null("") + " " + pl.col("body").fill_null("")).str.strip_chars())
        .filter(pl.col("text").str.len_chars() > 0)
    )

    train = r.filter(pl.col("rating") != 3)
    texts = train["text"].to_list()
    y = np.array((train["rating"] >= 4).to_list(), dtype=int)
    model = make_pipeline(
        TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True, stop_words="english"),
        LogisticRegression(max_iter=1000, class_weight="balanced"),
    )

    accuracy = None
    counts = np.bincount(y, minlength=2) if len(y) else np.zeros(2, dtype=int)
    folds = int(min(5, counts.min())) if (counts > 0).all() else 0
    if folds >= 2 and len(texts) >= 50:
        accuracy = float(cross_val_score(model, texts, y, cv=folds).mean())
    model.fit(texts, y)

    p_pos = model.predict_proba(r["text"].to_list())[:, 1]
    r = r.with_columns(p_positive=pl.Series(p_pos, dtype=pl.Float64)).with_columns(
        sentiment=pl.when(pl.col("p_positive") >= POS_T).then(pl.lit("Positive"))
        .when(pl.col("p_positive") <= NEG_T).then(pl.lit("Negative"))
        .otherwise(pl.lit("Mixed")),
        rating_sentiment=pl.when(pl.col("rating") >= 4).then(pl.lit("Positive"))
        .when(pl.col("rating") <= 2).then(pl.lit("Negative"))
        .otherwise(pl.lit("Mixed")),
    )

    names = np.array(model.steps[0][1].get_feature_names_out())
    order = np.argsort(model.steps[1][1].coef_[0])
    return {
        "reviews": r,
        "accuracy": accuracy,
        "n_train": len(train),
        "pos_terms": names[order[::-1][:12]].tolist(),
        "neg_terms": names[order[:12]].tolist(),
    }


def product_sentiment(r: pl.DataFrame) -> pl.DataFrame:
    schema = {"asin": pl.Utf8, "reviews": pl.UInt32, "positive_pct": pl.Float64,
              "negative_pct": pl.Float64, "avg_rating": pl.Float64}
    if r.is_empty():
        return pl.DataFrame(schema=schema)
    return r.group_by("asin").agg(
        pl.len().alias("reviews"),
        (pl.col("sentiment") == "Positive").mean().mul(100).alias("positive_pct"),
        (pl.col("sentiment") == "Negative").mean().mul(100).alias("negative_pct"),
        pl.col("rating").mean().alias("avg_rating"),
    )


# --------------------------------------------------------------------------- market basket (research)

def build_baskets(reviews: pl.DataFrame, min_items: int = 2) -> list[frozenset]:
    """Proxy baskets: the set of products one named customer reviewed.

    Reviews are not purchases. Author names are not unique IDs, so generic names are dropped and
    the output is research only.
    """
    grouped = (
        reviews.filter(pl.col("asin").is_not_null())
        .with_columns(author_key=pl.col("author").fill_null("").str.strip_chars().str.to_lowercase())
        .filter(~pl.col("author_key").is_in(GENERIC_AUTHORS))
        .group_by("author_key")
        .agg(pl.col("asin").unique())
    )
    return [frozenset(items) for items in grouped["asin"].to_list() if len(items) >= min_items]


def pair_rules(baskets: list[frozenset], min_count: int = 2, top: int = 50) -> pl.DataFrame:
    """Apriori up to 2-itemsets: support, confidence and lift for every pair seen at least min_count times."""
    schema = {"antecedent": pl.Utf8, "consequent": pl.Utf8, "pair_count": pl.Int64,
              "support": pl.Float64, "confidence": pl.Float64, "lift": pl.Float64}
    n = len(baskets)
    if n == 0:
        return pl.DataFrame(schema=schema)
    item_counts = Counter(i for b in baskets for i in b)
    pair_counts = Counter(p for b in baskets for p in combinations(sorted(b), 2))
    rows = []
    for (a, b), c in pair_counts.items():
        if c < min_count:
            continue
        for x, y in ((a, b), (b, a)):
            confidence = c / item_counts[x]
            rows.append({
                "antecedent": x,
                "consequent": y,
                "pair_count": c,
                "support": c / n,
                "confidence": confidence,
                "lift": confidence / (item_counts[y] / n),
            })
    if not rows:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame(rows, schema=schema).sort(["lift", "support"], descending=True).head(top)


# --------------------------------------------------------------------------- formatting

def inr(value) -> str:
    return "–" if is_missing(value) else f"₹{value:,.0f}"


def age_label(ts) -> str:
    if ts is None:
        return "unknown"
    hours = (datetime.now(UTC) - ts).total_seconds() / 3600
    return f"{hours:.0f} h ago" if hours < 48 else f"{hours / 24:.0f} d ago"


def style_fig(fig, height: int = 380):
    fig.update_layout(template="plotly_white", height=height, margin=dict(l=10, r=10, t=40, b=10),
                      legend=dict(orientation="h", y=1.1, x=0), paper_bgcolor="rgba(0,0,0,0)")
    return fig


# --------------------------------------------------------------------------- UI

def tab_prices(category_offers: pl.DataFrame, sel: dict, peers: pl.DataFrame, history: pl.DataFrame,
               city_name: str, city_slug: str, days: int):
    ours = sel["price"]
    lowest = peers["price"].min() if not peers.is_empty() else None
    st.subheader(f"Price trend · last {days} days · {city_name}")
    trend = daily_last_price(history, [sel["asin"]] + peers["asin"].to_list(), city_slug, days)
    mine = trend.filter(pl.col("asin") == sel["asin"])
    peer_median = (
        trend.filter(pl.col("asin") != sel["asin"]).group_by("day").agg(pl.col("price").median()).sort("day")
    )
    if mine.is_empty():
        st.info("No price history for this product in the selected window yet. Re-run the crawler to add points.")
    else:
        fig = go.Figure()
        fig.add_scatter(x=mine["day"].to_list(), y=mine["price"].to_list(), mode="lines+markers",
                        name="This product", line=dict(color=COLORS["us"], width=3))
        if not peer_median.is_empty():
            fig.add_scatter(x=peer_median["day"].to_list(), y=peer_median["price"].to_list(), mode="lines",
                            name="Peer median", line=dict(color=COLORS["peer"], dash="dash"))
        fig.update_yaxes(title="Price (₹)", tickprefix="₹")
        st.plotly_chart(style_fig(fig, 340))

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Competitor prices in this city")
        if peers.is_empty():
            st.info("No competing products in this subcategory for this city.")
        else:
            bars = pl.concat([
                pl.DataFrame({"label": [short_title(sel["title"], sel["asin"], 40)], "price": [ours], "us": [True]}),
                pl.DataFrame({
                    "label": [short_title(t, a, 40) for t, a in zip(peers["title"].to_list(), peers["asin"].to_list())],
                    "price": peers["price"].to_list(),
                    "us": [False] * peers.height,
                }),
            ]).sort("price")
            fig = px.bar(bars, x="price", y="label", orientation="h", color="us",
                         color_discrete_map={True: COLORS["us"], False: COLORS["peer"]},
                         text=[inr(v) for v in bars["price"].to_list()])
            fig.update_layout(showlegend=False)
            fig.update_xaxes(title="Price (₹)")
            fig.update_yaxes(title="")
            st.plotly_chart(style_fig(fig, 420))
    with right:
        st.subheader("Position")
        if lowest is None:
            st.info("No peer price to compare against.")
        else:
            gap = (ours / lowest - 1) * 100
            st.metric("Gap vs lowest peer", f"{gap:+.1f}%", help="Positive means this product costs more than the cheapest peer.")
            st.metric("Peers priced below this product", peers.filter(pl.col("price") < ours).height, f"of {peers.height}")
            st.metric("Peer median", inr(peers["price"].median()))

    st.subheader("Pricing-gap heatmap · subcategory × city")
    st.caption("Each cell is how far a city's price sits from that product's median across cities. "
               "Red = priced above the usual level, green = below.")
    gap = price_gap_matrix(category_offers)
    cities = [c for c in gap.columns if c != "label"]
    if len(cities) < 2 or gap.is_empty():
        st.info("Heatmap needs the same products crawled in at least two cities.")
    else:
        gap = gap.head(15)
        z = gap.select(cities).to_numpy().astype(float)
        fig = go.Figure(go.Heatmap(
            z=z, x=cities, y=gap["label"].to_list(),
            colorscale=[[0, COLORS["good"]], [0.5, "#ffffff"], [1, COLORS["bad"]]], zmid=0,
            text=np.round(z, 1), texttemplate="%{text:+.1f}%", colorbar=dict(title="% vs median"),
        ))
        st.plotly_chart(style_fig(fig, max(320, 34 * gap.height + 120)))

    st.subheader("Source listings")
    table = pl.concat([pl.DataFrame([sel]), peers], how="diagonal_relaxed")
    statuses = []
    for t in table["observed_at"].to_list():
        statuses.append("Unknown" if t is None else "Stale" if is_stale(t) else "Fresh")
    table = table.with_columns(
        status=pl.Series(statuses),
        link=pl.Series([product_url(a, u) for a, u in zip(table["asin"].to_list(), table["url"].to_list())]),
    )
    st.dataframe(
        table.select(["title", "price", "mrp", "discount_pct", "seller", "delivery_date", "observed_at", "status", "link"]),
        column_config={
            "title": st.column_config.TextColumn("Product", width="large"),
            "price": st.column_config.NumberColumn("Price (₹)", format="₹%d"),
            "mrp": st.column_config.NumberColumn("MRP (₹)", format="₹%d"),
            "discount_pct": st.column_config.NumberColumn("Discount", format="%d%%"),
            "observed_at": st.column_config.DatetimeColumn("Observed (UTC)", format="YYYY-MM-DD HH:mm"),
            "link": st.column_config.LinkColumn("Source", display_text="Open listing"),
        },
        hide_index=True,
    )


def tab_margin(sel: dict, peers: pl.DataFrame):
    st.subheader("Your landed cost and margin")
    st.caption("Enter your own costs. Prices come from the crawl; costs never do.")
    c1, c2, c3, c4, c5 = st.columns(5)
    unit_cost = c1.number_input("Unit cost (₹)", min_value=0.0, value=float(round(sel["price"] * 0.85)), step=100.0)
    shipping = c2.number_input("Inbound shipping (₹/unit)", min_value=0.0, value=150.0, step=10.0)
    duty = c3.number_input("Import duty (%)", min_value=0.0, value=0.0, step=0.5)
    gst = c4.number_input("GST (%)", min_value=0.0, value=DEFAULT_GST_PCT, step=1.0)
    fees = c5.number_input("Marketplace fees (% of net)", min_value=0.0, value=0.0, step=0.5)
    our_price = st.number_input("Your shelf price (₹, incl. GST)", min_value=0.0, value=float(sel["price"]),
                                step=10.0, key=f"price_{sel['asin']}")

    landed = landed_cost(unit_cost, shipping, duty)
    now = margin_at(our_price, landed, gst, fees)
    lowest = peers["price"].min() if not peers.is_empty() else None
    m1, m2, m3 = st.columns(3)
    m1.metric("Landed cost", inr(landed))
    m2.metric("Breakeven shelf price", inr(now["breakeven_price"]))
    m3.metric("Margin at your price", f"{now['margin_pct']:.1f}%", f"{inr(now['contribution'])} per unit")

    st.subheader("Scenarios")
    peer_prices = peers["price"].drop_nulls().to_list()
    scen = margin_scenarios(our_price, lowest, landed, gst, fees, peer_prices)
    st.dataframe(
        scen,
        column_config={
            "scenario": "Scenario",
            "price": st.column_config.NumberColumn("Shelf price", format="₹%d"),
            "net_revenue": st.column_config.NumberColumn("Net revenue", format="₹%d"),
            "contribution": st.column_config.NumberColumn("Contribution / unit", format="₹%d"),
            "margin_pct": st.column_config.NumberColumn("Margin", format="%.1f%%"),
            "breakeven_price": st.column_config.NumberColumn("Breakeven", format="₹%d"),
            "peers_cheaper": st.column_config.NumberColumn("Peers cheaper"),
            "risk": "Risk flag",
        },
        hide_index=True,
    )
    fig = px.bar(scen, x="scenario", y="margin_pct",
                 text=[f"{v:.1f}%" for v in scen["margin_pct"].to_list()],
                 color="risk", color_discrete_map={"OK": COLORS["good"]},
                 labels={"margin_pct": "Margin %", "scenario": ""})
    st.plotly_chart(style_fig(fig, 300))
    st.markdown('<div class="note"><b>Simulated.</b> Scenarios apply the price move to your cost '
                'base. They do not model customer demand or competitor response. "Peers cheaper" is '
                'an observed count from the crawl.</div>', unsafe_allow_html=True)


def tab_sentiment(sent: dict | None, products: pl.DataFrame, sel: dict):
    st.subheader("Review sentiment")
    if sent is None or sent["reviews"].is_empty():
        st.info("No reviews with text in the database yet.")
        return
    r = sent["reviews"]
    ps = product_sentiment(r)
    acc = sent["accuracy"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Reviews scored", f"{r.height:,}")
    c2.metric("Model accuracy (CV)", f"{acc:.0%}" if acc is not None else "n/a",
              help="5-fold cross-validation against star-rating labels (4–5 vs 1–2 stars).")
    c3.metric("Labelled for training", f"{sent['n_train']:,}")

    mine = ps.filter(pl.col("asin") == sel["asin"])
    if mine.is_empty():
        st.info("This product has no scored reviews yet.")
    else:
        m = mine.row(0, named=True)
        d1, d2, d3 = st.columns(3)
        d1.metric("Positive share", f"{m['positive_pct']:.0f}%")
        d2.metric("Negative share", f"{m['negative_pct']:.0f}%")
        d3.metric("Average stars", f"{m['avg_rating']:.1f}", f"{m['reviews']} reviews")

    st.subheader("Products in this subcategory")
    cat_asins = products.filter(pl.col("category") == sel["category"])["asin"].to_list()
    table = ps.filter(pl.col("asin").is_in(cat_asins)).join(products.select(["asin", "title"]), on="asin", how="left")
    table = table.with_columns(
        product=pl.Series([short_title(t, a) for t, a in zip(table["title"].to_list(), table["asin"].to_list())]),
    ).sort("reviews", descending=True)
    st.dataframe(
        table.select(["product", "reviews", "avg_rating", "positive_pct", "negative_pct"]),
        column_config={
            "product": st.column_config.TextColumn("Product", width="large"),
            "avg_rating": st.column_config.NumberColumn("Avg stars", format="%.1f"),
            "positive_pct": st.column_config.ProgressColumn("Positive", format="%.0f%%", min_value=0, max_value=100),
            "negative_pct": st.column_config.ProgressColumn("Negative", format="%.0f%%", min_value=0, max_value=100),
        },
        hide_index=True,
    )

    left, right = st.columns(2)
    with left:
        st.subheader("What drives positive reviews")
        st.write(", ".join(sent["pos_terms"]))
    with right:
        st.subheader("What drives negative reviews")
        st.write(", ".join(sent["neg_terms"]))

    st.subheader("Sample reviews for this product")
    sample = r.filter(pl.col("asin") == sel["asin"]).sort("p_positive")
    if sample.is_empty():
        st.info("No reviews for this product.")
    else:
        sample = pl.concat([sample.head(4), sample.tail(4)]).unique(maintain_order=True)
        st.dataframe(
            sample.with_columns(body=pl.col("body").fill_null("").str.slice(0, 220)).select(
                ["rating", "sentiment", "rating_sentiment", "p_positive", "title", "body"]),
            column_config={
                "rating": st.column_config.NumberColumn("Stars", format="%d★"),
                "sentiment": "Model says",
                "rating_sentiment": "Stars say",
                "p_positive": st.column_config.ProgressColumn("P(positive)", format="%.2f", min_value=0, max_value=1),
                "title": st.column_config.TextColumn("Title", width="medium"),
                "body": st.column_config.TextColumn("Review", width="large"),
            },
            hide_index=True,
        )
    st.caption("Labels are inferred from star ratings, so the model learns the rating signal. "
               "Use the 'Model says' column to spot reviews where text and stars disagree.")


def tab_basket(reviews: pl.DataFrame, products: pl.DataFrame):
    st.subheader("Market basket analysis · research")
    st.markdown('<div class="note"><b>Research only.</b> The crawl has no order data. Each "basket" is '
                'the set of products one named reviewer rated, so a rule means "people who reviewed X '
                'also reviewed Y". It is a co-review signal, not a sales or co-purchase measure.</div>',
                unsafe_allow_html=True)
    min_count = st.slider("Minimum co-reviewers per pair", 2, 10, 2)
    baskets = build_baskets(reviews)
    rules = pair_rules(baskets, min_count=min_count)
    c1, c2 = st.columns(2)
    c1.metric("Baskets (reviewers with 2+ products)", f"{len(baskets):,}")
    c2.metric("Rules found", f"{rules.height:,}")
    if rules.is_empty():
        st.info("No product pairs met the threshold. Lower it, or crawl more reviews.")
        return
    names = dict(zip(products["asin"].to_list(),
                     [short_title(t, a, 40) for t, a in zip(products["title"].to_list(), products["asin"].to_list())]))
    rules = rules.with_columns(
        if_reviewed=pl.Series([names.get(a, a) for a in rules["antecedent"].to_list()]),
        also_reviewed=pl.Series([names.get(a, a) for a in rules["consequent"].to_list()]),
    )
    fig = px.scatter(rules, x="support", y="confidence", size="lift", color="lift",
                     hover_data=["if_reviewed", "also_reviewed", "pair_count"],
                     color_continuous_scale="Blues", labels={"support": "Support", "confidence": "Confidence"})
    st.plotly_chart(style_fig(fig, 380))
    st.dataframe(
        rules.select(["if_reviewed", "also_reviewed", "pair_count", "support", "confidence", "lift"]),
        column_config={
            "if_reviewed": st.column_config.TextColumn("If a customer reviewed", width="large"),
            "also_reviewed": st.column_config.TextColumn("They also reviewed", width="large"),
            "pair_count": st.column_config.NumberColumn("Co-reviewers"),
            "support": st.column_config.NumberColumn("Support", format="%.3f"),
            "confidence": st.column_config.NumberColumn("Confidence", format="%.2f"),
            "lift": st.column_config.NumberColumn("Lift", format="%.2f"),
        },
        hide_index=True,
    )
    st.caption("Lift above 1 means the pair co-occurs more than chance. Support is the share of baskets "
               "containing both products.")


def tab_health(data: dict):
    st.subheader("Data health")
    counts = {
        "Products": data["products"].height,
        "City offers": data["offers"].height,
        "Price points (window)": data["history"].height,
        "Reviews": data["reviews"].height,
    }
    cols = st.columns(len(counts))
    for col, (label, value) in zip(cols, counts.items()):
        col.metric(label, f"{value:,}")

    total = data["offers"].height
    stale = sum(is_stale(t) for t in data["offers"]["observed_at"].to_list())
    missing = data["offers"]["price"].null_count()
    c1, c2 = st.columns(2)
    c1.metric("Offers older than the stale threshold", f"{stale:,} of {total:,}", f"threshold {STALE_HOURS:g} h")
    c2.metric("Offers without a price", f"{missing:,}")

    st.subheader("Recent crawl runs")
    if not data["runs"]:
        st.info("No crawl runs recorded.")
    else:
        rows = [{
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "status": run.get("status"),
            "stats": ", ".join(f"{k}: {v}" for k, v in (run.get("stats") or {}).items()),
        } for run in data["runs"]]
        st.dataframe(pl.DataFrame(rows), hide_index=True)


def main() -> None:
    st.set_page_config(page_title="RetailPilot · Retailer Dashboard", page_icon="📊", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="hero"><h1>Retailer Dashboard</h1>'
        '<p>Competitor prices, margin scenarios, review sentiment and basket research, '
        'built from the Amazon.in crawl.</p></div>',
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("Filters")
        history_days = st.slider("Price trend window (days)", 7, MAX_HISTORY_DAYS, 14)

    try:
        data = load_data(MONGO_URI, MONGO_DB, MAX_HISTORY_DAYS)
    except Exception as exc:  # pymongo raises several error types for "cannot connect"
        st.error(f"Cannot read MongoDB at {MONGO_URI} ({exc.__class__.__name__}). "
                 "Start it with `docker compose up -d` in amazon_ecomm_crawler.")
        st.stop()

    offers = data["offers"]
    if offers.is_empty():
        st.warning("No city offers in the database yet. Run the crawler first: "
                   "`python amazon_ecomm_crawler/crawler.py --city mumbai`.")
        st.stop()

    offers = offers.with_columns(
        label=pl.Series([f"{short_title(t, a, 45)} · {a}"
                         for t, a in zip(offers["title"].to_list(), offers["asin"].to_list())]),
    )
    priced = offers.filter(pl.col("price").is_not_null())

    with st.sidebar:
        city = st.selectbox("City", sorted(priced["city_name"].drop_nulls().unique().to_list()))
        city_offers = priced.filter(pl.col("city_name") == city)
        categories = ["All categories"] + sorted(city_offers["category"].drop_nulls().unique().to_list())
        category = st.selectbox("Category", categories)
        scope = city_offers if category == "All categories" else city_offers.filter(pl.col("category") == category)
        if scope.is_empty():
            st.info("No priced products here.")
            st.stop()
        labels = dict(zip(scope["asin"].to_list(), scope["label"].to_list()))
        asin = st.selectbox("Product", scope.sort("title")["asin"].to_list(), format_func=lambda a: labels[a])

    sel = scope.filter(pl.col("asin") == asin).row(0, named=True)
    peers = peers_of(city_offers, sel)
    sent = build_sentiment(data["reviews"]) if not data["reviews"].is_empty() else None
    ps = product_sentiment(sent["reviews"]) if sent is not None else product_sentiment(pl.DataFrame())
    mine_sent = ps.filter(pl.col("asin") == asin)

    lowest = peers["price"].min() if not peers.is_empty() else None
    gap = (sel["price"] / lowest - 1) * 100 if lowest else None
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Current price", inr(sel["price"]), f"MRP {inr(sel['mrp'])}" if not is_missing(sel["mrp"]) else None)
    k2.metric("Lowest peer", inr(lowest), f"{gap:+.1f}% vs you" if gap is not None else None, delta_color="inverse")
    k3.metric("Rating", f"{sel['rating']:.1f} ★" if not is_missing(sel["rating"]) else "–",
              f"{int(sel['rating_count']):,} ratings" if not is_missing(sel["rating_count"]) else None)
    k4.metric("Positive reviews", f"{mine_sent['positive_pct'][0]:.0f}%" if not mine_sent.is_empty() else "–")
    k5.metric("Listing observed", age_label(sel["observed_at"]))

    brand = sel["brand"] if isinstance(sel["brand"], str) else ""
    st.markdown(f"**{short_title(sel['title'], asin, 120)}**  ·  {brand}  ·  "
                f"[Open on Amazon.in]({product_url(asin, sel['url'])})")

    tabs = st.tabs(["Prices & competition", "Margin scenarios", "Ratings & sentiment",
                    "Market basket (research)", "Data health"])
    # The heatmap compares cities, so it uses every city's offers for the subcategory, not only the selected city.
    with tabs[0]:
        city_slug = city_offers.filter(pl.col("city_name") == city)["city"][0]
        tab_prices(priced.filter(pl.col("category") == sel["category"]), sel, peers, data["history"],
                   city, city_slug, history_days)
    with tabs[1]:
        tab_margin(sel, peers)
    with tabs[2]:
        tab_sentiment(sent, data["products"], sel)
    with tabs[3]:
        tab_basket(data["reviews"], data["products"])
    with tabs[4]:
        tab_health(data)


if __name__ == "__main__":
    main()
