from django.urls import path

from web import views

urlpatterns = [
    path("", views.landing, name="landing"),
    path("retailer/", views.retailer, name="retailer"),
    path("customer/", views.customer, name="customer"),
    path("crawler/health/", views.crawler_health, name="crawler_health"),
    path("api/cities/", views.api_cities, name="api_cities"),
    path("api/categories/", views.api_categories, name="api_categories"),
    path("api/category-search/", views.api_category_search, name="api_category_search"),
    path("api/recommendations/", views.api_recommendations, name="api_recommendations"),
    path("api/shown/", views.api_shown, name="api_shown"),
    path("api/interest/", views.api_interest, name="api_interest"),
    path("api/threads/", views.api_threads, name="api_threads"),
    path("api/threads/<str:thread_id>/messages/", views.api_thread_messages, name="api_thread_messages"),
    path("api/chat/", views.api_chat, name="api_chat"),
    path("api/chat/stream/", views.api_chat_stream, name="api_chat_stream"),
    path("api/chat/<str:thread_id>/answer/stream/", views.api_answer_stream, name="api_answer_stream"),
    path("api/chat/<str:thread_id>/answer/", views.api_answer, name="api_answer"),
]
