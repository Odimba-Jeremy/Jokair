# Problèmes liés aux boxes

- **Libération du box** – nécessite plusieurs clics ; la mise à jour du statut (`free` ↔ `occupied`) n’est pas toujours appliquée immédiatement.
- **Vérification du box lors du dispatch** – le code contrôle le `doctor_id` du box, mais ne rafraîchit pas le statut du box après le dispatch, ce qui peut laisser le box référencé comme occupé alors qu’il est déjà libéré.
- **Mise à jour du compteur/badge** – la suppression du cache global (`invalidate_cache()`) n’est pas ciblée ; le badge de la salle d’attente ne reflète pas correctement les changements de statut du box.
- **Gestion des collisions** – si deux dispatchs sont effectués quasi‑simultanément, le même box peut être attribué deux fois parce que le contrôle de disponibilité (`status != "free"`) n’est pas atomique.
- **Blueprint non enregistré** – Le Blueprint `workflow` défini dans `workflow.py` n’est jamais enregistré dans `app.py` (`app.register_blueprint`). Toutes les routes du workflow, dont `/api/workflow/queue`, sont donc inactives.
- **Endpoint `/api/workflow/queue` absent** – Aucun routeur `GET /api/workflow/queue` n’existe, d’où le `404` lors du pré‑flight CORS.
---

Cette liste regroupe les problèmes que nous avons pu identifier à partir des logs, du code source et des retours utilisateurs. Elle sert de base pour établir un plan de correction détaillé.
