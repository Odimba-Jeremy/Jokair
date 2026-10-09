# Backend Dedikka

Monolithe modulaire Python : **tout le code source du backend est directement dans ce dossier**, sans sous-dossier. Chaque fichier est un module métier ou technique : `main.py`, `accounts.py`, `database.py`, `config.py`.

## Démarrage local

Depuis `backend/` :

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e .
Copy-Item .env.example .env
py -m uvicorn main:app --reload --port 8001
```

Avec le site ouvert sur `127.0.0.1:8000`, l’API écoute sur `http://127.0.0.1:8001`. Vérifier `GET /api/v1/health` ou ouvrir `/docs`.

En développement, SQLite crée les tables `users` et `user_sessions` au démarrage. Pour la production, configurer `DATABASE_URL` avec PostgreSQL, appliquer des migrations et activer HTTPS avec `COOKIE_SECURE=true`.

## Authentification

- `GET /api/v1/auth/csrf` émet un jeton CSRF.
- `POST /api/v1/auth/register` crée un compte et ouvre une session.
- `POST /api/v1/auth/login` vérifie les identifiants et ouvre une session.
- `GET /api/v1/auth/me` renvoie le compte connecté.
- `POST /api/v1/auth/logout` révoque la session.

Les sessions sont conservées côté serveur ; le cookie de session est `HttpOnly` et seul son condensat est stocké en base. Les mots de passe sont hachés.

## Première publication de dédicace

- `POST /api/v1/uploads/images` envoie une image authentifiée au bucket `dedikka-images`.
- `POST /api/v1/pages` sauvegarde la dédicace et renvoie son URL publique.
- `GET /p/{slug}` affiche la page publiée.
- `POST /api/v1/pages/preview` produit l’aperçu de la page Dedicate.

Ajoute `SUPABASE_URL`, `SUPABASE_SECRET_KEY` et `SUPABASE_BUCKET` à l’environnement du backend. La clé secrète reste côté serveur. Exécute `schema.sql` dans le SQL Editor Supabase avant un déploiement en production.

## Render

Le fichier `../render.yaml` démarre Gunicorn sur le point d’entrée ASGI `main:app`, avec le worker Uvicorn. Dans Render, renseigne les variables marquées `sync: false` : `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `PUBLIC_BACKEND_URL` et `FRONTEND_ORIGINS`.
