# RetailPilot: Enhancements to Reach a 9/10

The current idea rates about 7/10 for an APJC-wide hackathon. The gaps are originality, regional relevance, and business value. The enhancements below target those gaps first, then polish the demo and trust signals that judges reward.

Each item lists what to add, why it matters, and a rough effort (S = a few hours, M = about a day, L = more than a day).

---

## 1. Pick a specific APJC wedge (Regional relevance: 5 → 9)

**What:** Launch the demo with two or three named markets, such as India (INR), Singapore (SGD), and Australia (AUD). Show retailers and listings from each, with prices in local currency.

**Why:** A regional judge will recognize a concrete market faster than a "global" pitch. Naming markets also forces the data model to handle currency and tax from the start.

**Effort:** M

---

## 2. Local cost realism: tax, duties, and shipping (Business value: 7 → 9)

**What:** Add fields for local tax (GST, VAT), import duty where it applies, and shipping cost to the retailer cost inputs. The margin calculation then uses landed cost, not just the unit price.

**Why:** Margin figures that ignore tax and shipping look naive to anyone who runs a retail business in the region. Getting this right is a clear signal of domain depth.

**Effort:** S

---

## 3. A margin scenario engine with a recommendation (Originality: 5 → 8)

**What:** For a product the retailer sells, show three scenarios side by side: **match the lowest competitor price**, **hold price**, and **raise price**. For each, show the projected margin, the estimated effect on share of comparable listings, and a risk flag (for example, "below breakeven").

**Why:** This turns the tool from a price viewer into a decision aid. It's the clearest answer to "what does this do that a spreadsheet can't?"

**Effort:** M

---

## 4. Evidence-backed recommendations with a confidence score (AI fit and trust: 7 → 9)

**What:** Each recommendation the assistant produces lists the listings it used, their observation times, the calculation it ran, and a confidence level based on match quality and data freshness.

**Why:** Judges can check every claim. This also directly addresses the "grounded, no invented numbers" requirement.

**Effort:** S

---

## 5. A visible agent trace (AI fit: 7 → 9)

**What:** In the UI, show the agent's steps as a collapsible trace: which tool was called, with what inputs, and what came back. Make it a toggle, so the main view stays clean.

**Why:** Judges see the agent using tools, not just producing answers. It is the single most convincing proof of agentic behavior in a short demo.

**Effort:** S

---

## 6. A pricing-gap heatmap as the visual centerpiece (Demo: 7 → 9)

**What:** A grid of laptop models by retailer, colored by how far each price sits from the market median. Clicking a cell opens the listing and its price history.

**Why:** One image explains the whole product in five seconds. A strong visual is often what judges remember after the demo.

**Effort:** M

---

## 7. Proactive insights, not only search (Originality: 8 → 9)

**What:** A daily "market brief" for the retailer: the three largest price drops among competitors, the models where they are most out of line, and any listing that went out of stock. Each item links to its evidence.

**Why:** It shows the product works without a question being asked, which is how a business tool should behave.

**Effort:** M

---

## 8. Published evaluation numbers (Judge appeal: 7 → 9)

**What:** Run the Step 10 tests on a labeled set and show three numbers on a slide: match precision, margin-calculation accuracy against a manual check, and grounding rate (the share of assistant numbers that trace to a tool result). Include one honest limitation next to each.

**Why:** Measured results beat claims. Judges who work in data and AI will weigh them heavily.

**Effort:** S (the test set is the real work)

---

## 9. Responsible data practice, stated plainly (Judge appeal: 7 → 8)

**What:** Add a short "Data sources and ethics" section to the proposal: which sources are used, how permission was checked, that robots.txt and terms are respected, that only advertised prices are shown, and that profit uses only the retailer's own costs.

**Why:** Scraping is a common concern for judges. A clear answer removes the worry and earns trust.

**Effort:** S

---

## 10. A named pilot story (Business value and presentation: 7 → 9)

**What:** Build the demo around one fictional but realistic retailer persona, for example "a three-store electronics chain in Singapore." Show their before-and-after: pricing decisions made with and without RetailPilot, with a clearly labeled simulated impact.

**Why:** A specific person and a before-and-after story make the value tangible. Label the numbers as simulated, since judges penalize unlabeled claims.

**Effort:** S

---

## 11. Customer side: needs-based matching (Originality and demo: 7 → 8)

**What:** Let the customer pick a use case (student, gaming, video editing, office travel). The assistant maps the use case to required specs and explains each recommendation in terms of those needs.

**Why:** It gives the customer experience the same grounding as the retailer side, and it's easy to show in the demo.

**Effort:** S

---

## 12. Multilingual answers (Regional relevance: 8 → 9)

**What:** Let the assistant answer in English plus one or two regional languages through Gemini, such as Hindi or Japanese. Keep product names and numbers in their original form.

**Why:** It's the most visible signal that the product was built for the region, not just tested there.

**Effort:** S (through Gemini) to M (with localized UI labels)

---

## 13. Scale story and architecture diagram (Technical credibility: 7 → 9)

**What:** Present the architecture as a repeatable pattern: adding a market means adding a collector and currency settings, not rewriting the system. Show it in one diagram.

**Why:** It answers the question judges always ask: "can this grow beyond the demo?"

**Effort:** S

---

## Priority order

If time is limited, do these in order:

| Priority | Enhancement | Main score gain |
|---|---|---|
| 1 | 1. APJC wedge with named markets | Regional relevance |
| 2 | 3. Margin scenario engine | Originality, business value |
| 3 | 4 + 5. Evidence-backed recommendations and visible agent trace | AI fit |
| 4 | 6. Pricing-gap heatmap | Demo |
| 5 | 8. Published evaluation numbers | Judge appeal |
| 6 | 2. Tax and shipping in margin | Business value |
| 7 | 10. Named pilot story | Presentation |

Items 1 through 5 are the ones that most directly move the score from 7 to 9. Items 6 through 10 add polish. Items 11 through 13 are useful if time remains.

## What to avoid

- **New categories** (phones, TVs). Expanding scope weakens the laptop story.
- **Multi-agent setups** for their own sake. One agent with clear tools is easier to explain and more reliable.
- **Unlabeled simulated numbers.** Every projection should say it is a simulation.
- **Features that aren't in the live demo.** Judges will ask to see them.
