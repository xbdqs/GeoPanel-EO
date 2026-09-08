class GeoPanelError(ValueError):
    """Expected, actionable failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")
