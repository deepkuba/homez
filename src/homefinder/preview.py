from jinja2 import Environment, PackageLoader, select_autoescape

from homefinder.domain.models import Listing, ListingSnapshot

_ENVIRONMENT = Environment(
    loader=PackageLoader("homefinder"),
    autoescape=select_autoescape(enabled_extensions=("html",), default=True),
)


def render_preview_card(listing: Listing, snapshot: ListingSnapshot) -> str:
    template = _ENVIRONMENT.get_template("listing_card.html")
    return template.render(
        listing=listing,
        snapshot=snapshot,
        formatted_price=_format_pln(snapshot.price_minor),
        formatted_area=format(snapshot.area_sqm.normalize(), "f"),
    )


def _format_pln(minor_units: int) -> str:
    whole, fraction = divmod(minor_units, 100)
    grouped = f"{whole:,}".replace(",", "\N{NO-BREAK SPACE}")
    return f"{grouped},{fraction:02d}\N{NO-BREAK SPACE}zł"
