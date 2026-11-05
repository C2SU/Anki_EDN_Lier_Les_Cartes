# -*- coding: utf-8 -*-
"""
Anki EDN Onboarding & Installation Guide
Presents a structured, sober, and comprehensive guide for first-time Anki EDN setup,
incorporating essential workflow practices from the official EDN tutorial.
"""
from __future__ import annotations

import os
from aqt import mw, gui_hooks
from aqt.qt import (
    QApplication, QDialog, QDialogButtonBox, QTextBrowser,
    QTimer, QVBoxLayout, QWidget, QPalette
)

GUIDE_VERSION = "2026.10.27"


def is_dark_mode() -> bool:
    """Detect whether dark mode is currently active."""
    try:
        from aqt.theme import theme_manager
        if theme_manager and hasattr(theme_manager, "night_mode"):
            return bool(theme_manager.night_mode)
    except Exception:
        pass
    try:
        if mw and hasattr(mw, "theme_manager") and hasattr(mw.theme_manager, "night_mode"):
            return bool(mw.theme_manager.night_mode)
    except Exception:
        pass
    try:
        app = QApplication.instance()
        if app:
            bg_col = app.palette().color(QPalette.ColorRole.Window)
            return bg_col.lightness() < 128
    except Exception:
        pass
    return False


def get_guide_css(dark: bool) -> str:
    """Return sleek, minimalist CSS without any emojis."""
    if dark:
        bg_main = "#1e2227"
        bg_card = "#21252b"
        bg_code = "#282c34"
        text_main = "#abb2bf"
        text_muted = "#7f848e"
        accent = "#61afef"
        border_col = "#3b4048"
        kbd_bg = "#2c313a"
    else:
        bg_main = "#ffffff"
        bg_card = "#f8f9fa"
        bg_code = "#f1f3f5"
        text_main = "#212529"
        text_muted = "#6c757d"
        accent = "#1971c2"
        border_col = "#dee2e6"
        kbd_bg = "#e9ecef"

    return f"""
    body {{
        background-color: {bg_main};
        color: {text_main};
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        font-size: 13px;
        line-height: 1.6;
        margin: 0;
        padding: 16px 20px;
    }}
    h1 {{
        font-size: 19px;
        font-weight: 700;
        color: {accent};
        margin-top: 0;
        margin-bottom: 8px;
        border-bottom: 2px solid {border_col};
        padding-bottom: 6px;
    }}
    h2 {{
        font-size: 15px;
        font-weight: 600;
        color: {text_main};
        margin-top: 20px;
        margin-bottom: 8px;
        border-bottom: 1px solid {border_col};
        padding-bottom: 4px;
    }}
    h3 {{
        font-size: 13.5px;
        font-weight: 600;
        color: {accent};
        margin-top: 14px;
        margin-bottom: 6px;
    }}
    p, li {{
        color: {text_main};
    }}
    ul, ol {{
        margin-top: 6px;
        margin-bottom: 12px;
        padding-left: 22px;
    }}
    li {{
        margin-bottom: 5px;
    }}
    .intro-box {{
        background-color: {bg_card};
        border: 1px solid {border_col};
        border-radius: 6px;
        padding: 12px 16px;
        margin-bottom: 16px;
    }}
    .stat-row {{
        display: flex;
        gap: 16px;
        margin-top: 8px;
        margin-bottom: 6px;
    }}
    .stat-badge {{
        background-color: {bg_code};
        border: 1px solid {border_col};
        border-radius: 4px;
        padding: 4px 10px;
        font-size: 12px;
        font-weight: 600;
    }}
    .code-block {{
        background-color: {bg_code};
        border: 1px solid {border_col};
        border-radius: 5px;
        padding: 8px 12px;
        font-family: "Consolas", "Courier New", monospace;
        font-size: 12px;
        color: {accent};
        margin: 8px 0;
        user-select: all;
    }}
    .callout {{
        background-color: {bg_card};
        border-left: 4px solid {accent};
        padding: 8px 14px;
        margin: 12px 0;
        border-radius: 0 4px 4px 0;
    }}
    kbd {{
        background-color: {kbd_bg};
        border: 1px solid {border_col};
        border-radius: 3px;
        box-shadow: 0 1px 1px rgba(0, 0, 0, 0.1);
        font-family: inherit;
        font-size: 11px;
        padding: 2px 6px;
    }}
    a {{
        color: {accent};
        text-decoration: none;
    }}
    a:hover {{
        text-decoration: underline;
    }}
    """


def get_guide_html(dark: bool = False, updated: bool = False) -> str:
    """Generate comprehensive Anki EDN onboarding guide HTML."""
    css = get_guide_css(dark)

    changelog_section = ""
    if updated:
        changelog_section = """
        <div class="callout">
            <b>Mise à jour d'Anki EDN :</b> Vos outils et fonctionnalités ont été actualisés.
            Ce guide récapitule les étapes d'installation et les bonnes pratiques de synchronisation.
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>{css}</style>
    </head>
    <body>
        <h1>Guide d'installation et de synchronisation — Anki EDN</h1>
        {changelog_section}

        <div class="intro-box">
            <b>Bienvenue dans le paquet Anki EDN</b><br>
            Ce projet collaboratif repose sur la méthodologie de Léo Picat et propose un jeu complet
            de flashcards adaptées à la préparation des épreuves dématérialisées nationales (EDN).
            <div class="stat-row">
                <span class="stat-badge">~18 000 cartes</span>
                <span class="stat-badge">367 items EDN couverts</span>
                <span class="stat-badge">Algorithme FSRS optimisé</span>
                <span class="stat-badge">Liens bidirectionnels</span>
            </div>
        </div>

        <h2>1. Abonnement au paquet AnkiCollab</h2>
        <ol>
            <li>Créez un compte gratuit sur <a href="https://ankicollab.com">ankicollab.com</a>.</li>
            <li>Dans Anki, connectez-vous via <b>AnkiCollab &gt; Login</b>.</li>
            <li>Allez dans <b>AnkiCollab &gt; Manage Subscriptions</b> (ou <i>Settings</i>).</li>
            <li>Dans le champ <i>Share code</i>, collez le code de partage du paquet officiel puis cliquez sur <b>Submit</b> :</li>
        </ol>
        <div class="code-block">mango-may-georgia-eleven-moon-orange</div>
        <ol start="5">
            <li>Cliquez sur <b>Global Settings</b> en bas de la fenêtre et cochez les options recommandées :
                <ul>
                    <li><b>Update Decks on startup</b> : réception automatique des mises à jour au lancement d'Anki ;</li>
                    <li><b>Automatically suspend new Cards</b> : les nouvelles cartes ajoutées au paquet sont suspendues pour ne pas polluer vos séances en cours ;</li>
                    <li><b>Auto-sync on Anki startup and close</b> et <b>Synchronize media files</b>.</li>
                </ul>
            </li>
            <li>Cliquez sur <b>Save</b> pour valider.</li>
        </ol>

        <h2>2. Activation des cartes liées (Réparation des NID)</h2>
        <p>
            L'add-on des cartes liées permet de naviguer instantanément entre les notions connexes
            (sommaires, sous-items, étiologies).
        </p>
        <ol>
            <li>Dans la barre de menus supérieure, cliquez sur <b>Anki EDN</b>.</li>
            <li>Sélectionnez <b>Réparer les NID</b>.</li>
            <li>L'analyseur synchronise les identifiants locaux. Une confirmation s'affiche dès la fin de l'opération.</li>
        </ol>

        <h2>3. Paramètres de révision recommandés &amp; FSRS</h2>
        <p>
            Le paquet est calibré pour l'algorithme FSRS (Free Spaced Repetition Scheduler) nativement intégré dans Anki.
            Ouvrez les options du paquet (icône d'engrenage à droite du nom du paquet principal) :
        </p>
        <ul>
            <li><b>FSRS</b> : activé. Taux de rétention souhaité recommandé : <b>88 % (0.88)</b> pour concilier mémorisation durable et temps de révision maîtrisé.</li>
            <li><b>Nouvelles cartes par jour</b> : 25 à 40 (maximum conseillé : 50).</li>
            <li><b>Révisions maximales par jour</b> : illimité (ou 500+) pour ne jamais masquer de dette de révision.</li>
            <li><b>Enfouir les cartes sœurs (Bury siblings)</b> : activé pour les nouvelles cartes et les révisions.</li>
            <li><b>Optimisation mensuelle</b> : rendez-vous une fois par mois dans la section FSRS des options et cliquez sur <b>Optimiser</b>.</li>
        </ul>

        <h2>4. Méthodologie et prise en main</h2>
        <div class="callout">
            <b>Règle clé :</b> Il est indispensable de lire et de comprendre le cours médical avant
            de lancer les cartes Anki correspondantes.
        </div>
        <ol>
            <li><b>Suspendre le paquet complet au départ</b> :
                Ouvrez l'explorateur (<kbd>B</kbd>), sélectionnez l'étiquette <code>EDN</code> dans le volet gauche,
                faites <kbd>Ctrl+A</kbd> puis <kbd>Ctrl+J</kbd> pour suspendre toutes les cartes.
            </li>
            <li><b>Désuspendre par item</b> :
                Une fois votre cours assimilé, recherchez l'étiquette <code>EDN::item-XXX</code>,
                sélectionnez les cartes concernées et activez-les via <kbd>Ctrl+J</kbd>.
            </li>
        </ol>

        <h2>5. Synchronisation et gestion des conflits (PC / Mobile)</h2>
        <ul>
            <li>
                <b>Ordre de synchronisation :</b> Synchronisez toujours en priorité sur votre ordinateur,
                puis ensuite sur votre mobile/tablette (AnkiDroid / AnkiMobile).
            </li>
            <li>
                <b>Conflits de modèles ("Envoyer vers AnkiWeb" / "Télécharger depuis AnkiWeb") :</b><br>
                Lorsqu'une mise à jour de modèle de carte intervient :
                <ul>
                    <li>Sur votre ordinateur : choisissez systématiquement <b>Envoyer vers AnkiWeb</b>.</li>
                    <li>Sur votre téléphone / tablette : choisissez <b>Télécharger depuis AnkiWeb</b>.</li>
                </ul>
            </li>
        </ul>

        <h2>6. Protection de vos modifications personnelles</h2>
        <p>
            Vous pouvez personnaliser librement vos cartes (recto, verso, notes).
            Pour empêcher AnkiCollab d'écraser vos modifications lors des mises à jour :
        </p>
        <ul>
            <li>Activez l'option dans le menu <b>Anki EDN &gt; Protéger les champs modifiés (AnkiCollab)</b> ou dans les <b>Paramètres EDN</b>.</li>
            <li>Vos champs personnalisés sont alors balisés avec des étiquettes de protection de champs.</li>
            <li>Le champ <i>Erreurs faites</i> est quant à lui strictement personnel et n'est jamais écrasé.</li>
        </ul>

        <h2>7. Liens utiles et communauté</h2>
        <ul>
            <li>Discord officiel Anki EDN : <a href="https://discord.gg/2A7zHAEBYt">discord.gg/2A7zHAEBYt</a></li>
            <li>Tutoriel web complet : <a href="https://c2su.github.io/Anki_EDN/Anki_EDN.html">c2su.github.io/Anki_EDN/Anki_EDN.html</a></li>
            <li>Documentation de Léo Picat : <a href="https://picat.fr/2024/06/02/paquet-anki-edn.html">picat.fr</a></li>
            <li>Groupe Facebook des externes : <a href="https://facebook.com/groups/anki.ecni">facebook.com/groups/anki.ecni</a></li>
        </ul>
        <p style="font-size: 11.5px; color: #7f848e; margin-top: 16px;">
            Vous pouvez rouvrir ce guide à tout moment depuis le menu <b>Anki EDN &gt; Guide d'installation EDN...</b>
        </p>
    </body>
    </html>
    """


def show_onboarding_guide(parent=None, updated: bool = False) -> None:
    """Display the onboarding guide in a modal dialog."""
    try:
        from .shared_menu import get_config, save_config
    except ImportError:
        from shared_menu import get_config, save_config

    dark = is_dark_mode()
    d = QDialog(parent or mw)
    d.setWindowTitle("Guide d'installation et de synchronisation — Anki EDN")

    lay = QVBoxLayout(d)
    lay.setContentsMargins(12, 12, 12, 12)
    lay.setSpacing(10)

    view = QTextBrowser()
    view.setOpenExternalLinks(True)
    view.setHtml(get_guide_html(dark=dark, updated=updated))
    lay.addWidget(view)

    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
    ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
    if ok_btn:
        ok_btn.setText("J'ai compris")
    bb.accepted.connect(d.accept)
    lay.addWidget(bb)

    d.resize(720, 680)

    # Persist that user has seen this version
    cfg = get_config()
    cfg["guide_seen_version"] = GUIDE_VERSION
    save_config(cfg)

    d.exec()


def _on_profile_open() -> None:
    """Prompt user once on profile open if this version of the guide was not seen."""
    if not mw:
        return
    try:
        from .shared_menu import get_config
    except ImportError:
        from shared_menu import get_config

    cfg = get_config()
    seen = cfg.get("guide_seen_version")
    if seen == GUIDE_VERSION:
        return

    updated = bool(seen)
    timer = QTimer(mw)

    def tick() -> None:
        if mw.col is None:
            timer.stop()
        elif not mw.progress.busy() and QApplication.activeModalWidget() is None:
            timer.stop()
            show_onboarding_guide(parent=mw, updated=updated)

    timer.timeout.connect(tick)
    QTimer.singleShot(2500, lambda: timer.start(1000))


def init_guide_hooks() -> None:
    """Register profile opening hook."""
    gui_hooks.profile_did_open.append(_on_profile_open)
