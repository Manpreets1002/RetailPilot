from django.urls import include, path

urlpatterns = [
    path("", include("web.urls")),
]
