# Configuration — Lier les cartes EDN

Ce module gère le maillage transversal entre cartes de révision (relations diagnostiques, pièges sémiologiques, rappels physiopathologiques) et le panneau d'assistance contextuelle dans l'éditeur.

## Options de l'éditeur et des liaisons

- `assistant_panel_open` (booléen) : mémorise l'ouverture par défaut du volet latéral d'assistance à la rédaction dans la fenêtre d'édition.
- `description_intelligente` (booléen) : active la génération ou l'affichage textuel synthétique du contexte médical lors du survol ou de l'association d'une carte liée.
- `suggestions_de_liens` (booléen) : propose automatiquement des cartes complémentaires ou en relation étroite lors de l'édition.
- `gui_mirror_cb_state` (booléen) : état par défaut de la case à cocher créant un lien réciproque (miroir) dans la carte distante associée.
- `gui_recto_only_cb_state` (booléen) : état par défaut de la case à cocher limitant la visibilité du lien au seul côté question (recto).
- `gui_trigger` (chaîne) : préfixe textuel déclencheur permettant de cibler ou insérer un lien vers un identifiant de note (par défaut `"nid:"`).
- `hidden_preview_sections` (liste de chaînes) : conteneurs HTML et sections masqués dans la fenêtre de prévisualisation rapide afin de conserver une vue concise (ex: `sourcesMegaContainer`, `commentsMegaContainer`, `tagsMegaContainer`).
- `mirror_link_on_paste` (booléen) : crée automatiquement la liaison bidirectionnelle symétrique lors du collage direct d'un identifiant ou lien de carte dans l'éditeur.
