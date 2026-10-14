"""Route modules, one per resource."""

from . import applications, companies, feed, insights, postings, system

ROUTERS = [
    feed.router,
    postings.router,
    applications.router,
    companies.router,
    insights.router,
    system.router,
]

__all__ = ["ROUTERS"]
