"""Root URL configuration."""

from django.contrib import admin
from django.contrib.auth.decorators import login_not_required
from django.urls import include
from django.urls import path

from reellife import health

urlpatterns = [
    path("healthz", login_not_required(health.healthz), name="healthz"),
    path("readyz", login_not_required(health.readyz), name="readyz"),
    path("auth/", include("accounts.urls")),
    path("admin/", admin.site.urls),
    path("", include("cues.urls")),
]
