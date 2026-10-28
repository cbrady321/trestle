from trestle.plugin.surface import Context, trestle


@trestle
def drop_in(ctx: Context, value: str = "ok") -> dict[str, str]:
    return {"value": value}
