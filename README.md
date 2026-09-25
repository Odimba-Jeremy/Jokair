# 🏥 I-HUB — Backend API

> Système de gestion hospitalière · Backend Flask · Déployé sur Render

![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.x-black?logo=flask)
![Supabase](https://img.shields.io/badge/Supabase-PostgreSQL-green?logo=supabase)
![Render](https://img.shields.io/badge/Deployed%20on-Render-46E3B7?logo=render)
![License](https://img.shields.io/badge/License-Private-red)

---

## 📋 Description

**I-HUB** est un système complet de gestion hospitalière conçu pour les hôpitaux en République Démocratique du Congo.  
Le backend expose une **API REST** consommée par le frontend JavaScript pur (aucun framework).

### Fonctionnalités principales
- 👤 Gestion des patients (admission, dossier médical, hospitalisations)
- 🩺 Consultations, soins infirmiers, prescriptions
- 💊 Pharmacie — délivrance et suivi des médicaments
- 🧪 Laboratoire — résultats d'analyses
- 🏥 Maternité & Pédiatrie
- 💰 Facturation bidevise (USD / Franc Congolais)
- 📊 Statistiques et rapports financiers en temps réel
- 🔐 Authentification multi-rôles (Admin, Médecin, Infirmier, Réception, Pharmacie, Labo)

---

## 🛠️ Stack Technique

| Couche | Technologie |
|--------|-------------|
| Framework | **Python Flask** |
| Base de données | **Supabase (PostgreSQL)** |
| Authentification | **Supabase Auth + JWT** |
| Déploiement | **Render** (Web Service) |
| Devise de référence | **USD** (taux FC dynamique depuis la DB) |

---

## 📁 Structure des Fichiers

```
backend/
├── app.py              # Point d'entrée principal — initialisation, helpers, routes de base
├── auth.py             # Authentification, gestion des sessions et des rôles
├── patients.py         # CRUD patients, dossiers médicaux, hospitalisations
├── medical.py          # Soins infirmiers, prescriptions médicales, actes
├── workflow.py         # Workflows : consultations, admissions, lignes de compte
├── billing_api.py      # Facturation, paiements bidevises, statistiques financières
├── pharmacy.py         # Délivrance médicaments, stock pharmacie
├── laboratory.py       # Résultats d'analyses, demandes labo
├── reports.py          # Rapports, taux de change, exports
├── admin.py            # Administration système, utilisateurs, configuration
├── doctor.py           # Interface médecin — consultations, ordonnances
├── maternity.py        # Gestion maternité
├── pediatrics.py       # Gestion pédiatrie
├── patient_portal.py   # Portail patient
├── events.py           # Événements temps réel (SSE)
├── ai.py               # Fonctionnalités IA (assistant clinique)
└── test.py             # Scripts de test
```

---

## ⚙️ Variables d'Environnement

Créez un fichier `.env` à la racine du dossier `backend/` (ou configurez-les dans Render) :

```env
# Supabase
SUPABASE_URL=https://xxxxxxxxxxxxxxxx.supabase.co
SUPABASE_KEY=your_supabase_service_role_key

# Flask
FLASK_SECRET_KEY=your_secret_key_here
FLASK_ENV=production

# (Optionnel) Port local
PORT=5000
```

> ⚠️ **Ne jamais committer le fichier `.env`** — il est dans `.gitignore`.

---

## 🚀 Déploiement sur Render

### 1. Prérequis
- Compte [Render](https://render.com)
- Dépôt GitHub connecté à Render

### 2. Configuration du Web Service
| Champ | Valeur |
|-------|--------|
| **Environment** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `gunicorn app:app` |
| **Root Directory** | `backend` |

### 3. Variables d'environnement
Ajouter `SUPABASE_URL`, `SUPABASE_KEY`, et `FLASK_SECRET_KEY` dans **Environment → Add Environment Variable** sur Render.

### 4. Déploiement automatique
Chaque `git push` sur la branche `main` déclenche un redéploiement automatique.

---

## 📡 Routes API Principales

### 🔐 Authentification
| Méthode | Route | Description |
|---------|-------|-------------|
| `POST` | `/api/auth/login` | Connexion utilisateur |
| `POST` | `/api/auth/logout` | Déconnexion |
| `GET` | `/api/auth/me` | Profil utilisateur courant |

### 👤 Patients
| Méthode | Route | Description |
|---------|-------|-------------|
| `GET` | `/api/patients` | Liste des patients |
| `POST` | `/api/patients` | Créer un patient |
| `GET` | `/api/patients/<id>` | Détail d'un patient |
| `PATCH` | `/api/patients/<id>` | Mettre à jour un patient |

### 💰 Facturation & Paiements
| Méthode | Route | Description |
|---------|-------|-------------|
| `GET` | `/api/billing/stats` | Statistiques financières du jour |
| `GET` | `/api/billing/account/<patient_id>` | Compte patient (solde, lignes, transactions) |
| `POST` | `/api/billing/pay/<patient_id>` | Enregistrer un paiement (USD ou FC) |
| `GET` | `/api/exchange-rate` | Taux de change USD/FC actif |

### 💊 Pharmacie
| Méthode | Route | Description |
|---------|-------|-------------|
| `GET` | `/api/pharmacy/stock` | Stock disponible |
| `POST` | `/api/pharmacy/deliver` | Délivrer un médicament |

### 🧪 Laboratoire
| Méthode | Route | Description |
|---------|-------|-------------|
| `GET` | `/api/laboratory/requests` | Demandes d'analyses |
| `POST` | `/api/laboratory/results` | Enregistrer des résultats |

### 📊 Rapports
| Méthode | Route | Description |
|---------|-------|-------------|
| `GET` | `/api/reports/daily` | Rapport journalier |
| `GET` | `/api/reports/patients` | Statistiques patients |

---

## 💱 Règles Métier — Facturation

### Devise de référence
- Toutes les transactions sont stockées en **USD**.
- Le taux USD/FC est lu **dynamiquement** depuis la table `exchange_rates` en base de données.
- **Aucun taux n'est codé en dur** dans le code source.

### Compte patient
- Le solde d'un patient = `Σ(lignes de compte)` − `Σ(paiements)`
- Les **factures** sont des documents d'impression uniquement — elles ne créent pas de dette.
- Un paiement ne peut pas dépasser le solde dû (anti-surpaiement).

### Idempotence
- Chaque acte et paiement porte une clé unique encodée dans le champ `description` : `[idemp:KEY]`
- Les doublons (double-clic, double-soumission réseau) sont détectés et ignorés.

---

## 🏃 Démarrage Local

```bash
# 1. Cloner le dépôt
git clone https://github.com/votre-username/i-hub-backend.git
cd i-hub-backend/backend

# 2. Créer un environnement virtuel
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac

# 3. Installer les dépendances
pip install -r requirements.txt

# 4. Configurer les variables d'environnement
cp .env.example .env
# Éditer .env avec vos clés Supabase

# 5. Lancer le serveur
python app.py
# ou avec gunicorn :
gunicorn app:app --reload
```

Le serveur démarre sur `http://localhost:5000`.

---

## 🔒 Sécurité

- Toutes les routes sensibles sont protégées par **JWT Supabase**.
- Les rôles sont vérifiés à chaque requête (Admin, Médecin, Infirmier, Réception, Pharmacie, Labo).
- Les clés Supabase sont stockées exclusivement dans les variables d'environnement.

---

## 📞 Contact

Projet **I-HUB** — Système de gestion hospitalière  
Développé pour les établissements de santé en RDC 🇨🇩
