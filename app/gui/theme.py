"""Shared visual constants: theme names, log-level colors, status colors."""

APP_TITLE = "AAOS Vehicle Property Simulator"
APP_ICON_TEXT = "\U0001F697"  # car emoji, used in window title / headers

# Native ttkbootstrap 2.x theme names (verified against Style().theme_names()
# for the installed version - avoid pre-2.0 legacy aliases like "flatly" /
# "cosmo", which still work but emit a DeprecationWarning and are slated
# for removal in ttkbootstrap 3.0).
DARK_THEMES = ["darkly", "nord-dark", "dracula-dark", "gruvbox-dark", "tokyo-night-dark", "one-dark"]
LIGHT_THEMES = ["bootstrap-light", "nord-light", "sandstone-light", "minty-light", "pydata-light"]
AVAILABLE_THEMES = DARK_THEMES + LIGHT_THEMES

# Android logcat priority letters -> (foreground, optional background)
LOG_LEVEL_COLORS = {
    "V": ("#9aa5b1", None),
    "D": ("#4fc3f7", None),
    "I": ("#66bb6a", None),
    "W": ("#ffb74d", None),
    "E": ("#ef5350", None),
    "F": ("#ffffff", "#b71c1c"),
    "S": ("#ce93d8", None),
}
LOG_LEVEL_LABELS = {
    "V": "Verbose", "D": "Debug", "I": "Info",
    "W": "Warning", "E": "Error", "F": "Fatal", "S": "Silent",
}

BOOTSTYLE_OK = "success"
BOOTSTYLE_WARN = "warning"
BOOTSTYLE_ERROR = "danger"
BOOTSTYLE_MUTED = "secondary"
BOOTSTYLE_INFO = "info"

def apply_treeview_theme(style) -> None:
    """Work around ttkbootstrap 2.x dark themes leaving `fieldbackground`
    unset on the "Treeview" style - without this, the empty area below a
    Treeview's rows renders with the platform's default (usually white)
    background instead of the theme's surface color. Must be re-applied
    after every theme switch since each ttk theme has its own style db.
    """
    colors = style.colors
    style.configure(
        "Treeview",
        background=colors.bg,
        fieldbackground=colors.bg,
        foreground=colors.fg,
    )
    style.map(
        "Treeview",
        background=[("selected", colors.selectbg)],
        foreground=[("selected", colors.selectfg)],
    )


def apply_notebook_theme(style) -> None:
    """Enlarge notebook tab hit-targets. The stock ttk padding (a few px)
    makes tabs fiddly to click, especially with an icon+text label -
    applies to every ttk.Notebook in the app (top-level and the Testing
    tab's inner notebook both use the same default "TNotebook" style).
    Must be re-applied after every theme switch, same as the Treeview fix.
    """
    style.configure("TNotebook.Tab", padding=(18, 10), font=("Segoe UI", 10))


TAB_ICONS = {
    "dashboard": "\U0001F3E0 ",
    "properties": "\U0001F4CB ",
    "logcat": "\U0001F5A5 ",
    "testing": "\U0001F9EA ",
    "screenshot": "\U0001F4F7 ",
    "processes": "\U0001F4CA ",
    "apk_install": "\U0001F4E6 ",
    "settings": "⚙ ",
    "about": "ℹ️ ",
}
