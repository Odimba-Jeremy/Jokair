# I-HUB — Passation de la fragmentation backend

Date : 2026-09-14  
État : migration commencée, application encore majoritairement monolithique.

## Objectif

Transformer progressivement `backend/app.py` en modules métier sans modifier les URLs API consommées par les frontends ni les tables Supabase existantes.

Architecture cible :

```text
backend/
├── app.py          # création Flask, cache, CORS, hooks, enregistrement des modules
├── auth.py         # authentification et sessions
├── patients.py     # patients, rendez-vous, identifiants, codes QR/barres
├── doctor.py       # consultations et prescriptions médicales
├── workflow.py     # file d'attente, SV, dispatch, box, soins, hospitalisation
├── laboratory.py   # examens, résultats, prélèvements, PDF laboratoire
├── pharmacy.py     # stock, délivrance, mouvements, ventes pharmacie
├── billing.py      # factures, comptes patients, paiements, tarifs
├── maternity.py    # grossesses, suivi prénatal, accouchements
├── reports.py      # rapports, dashboard, notifications, métriques
└── shared.py       # utilitaires réellement partagés, à créer lors de la migration
```

## État actuel

### Fichier monolithique

- `backend/app.py` contient encore l'essentiel de l'application : environ 5 100 lignes et les routes de tous les modules.
- Il contient aussi des fonctions globales partagées : client Supabase, cache, décorateurs d'autorisation, conversion de données, facturation, audit et helpers patients.
- Ne pas déplacer ces fonctions au hasard : plusieurs routes de métiers différents les utilisent.

### Migration déjà terminée : Auth

Le module `backend/auth.py` existe et possède réellement les routes suivantes :

| Route | Méthode | Comportement |
|---|---:|---|
| `/api/auth/login` | POST | Authentifie l'utilisateur et retourne le token |
| `/api/auth/register` | POST | Crée un utilisateur public autorisé |
| `/api/auth/logout` | POST | Ajoute l'audit de déconnexion |
| `/api/auth/me` | GET | Retourne l'utilisateur connecté |

`app.py` enregistre ce module à la fin du chargement avec `register_auth_routes(...)`.

Le module reçoit explicitement ses dépendances (`supabase`, `TABLES`, `ROLES`, `fast_json`, `token_required`, etc.). C'est volontaire : cela évite `auth.py -> app.py -> auth.py`, donc les imports circulaires.

Les anciennes routes Auth ont été retirées de `app.py`. La syntaxe a été validée avec :

```powershell
python -m py_compile backend/app.py backend/auth.py
```

## Règle de migration obligatoire

Pour chaque prochain module :

1. Copier la logique complète des routes concernées dans le nouveau fichier.
2. Créer `register_<module>_routes(app, *, ...)`.
3. Injecter uniquement les dépendances nécessaires depuis `app.py`.
4. Enregistrer le Blueprint dans `app.py` avant le bloc `if __name__ == "__main__"`.
5. Retirer les anciennes routes correspondantes de `app.py` dans le même changement.
6. Vérifier qu'aucune URL n'est déclarée deux fois.
7. Compiler puis tester les flux réels concernés.

Ne pas importer `app`, `supabase`, `cache` ou des fonctions d'`app.py` directement depuis un module métier. Utiliser l'injection de dépendances déjà utilisée par `auth.py`.

## Ordre recommandé

1. `patients.py`
   - `/api/patients...`
   - rendez-vous liés au patient, QR/barcode.
   - Attention aux fonctions de visibilité par rôle et aux identifiants hospitaliers.

2. `workflow.py`
   - file d'attente, signes vitaux, dispatch, box, consultations workflow, soins, hospitalisation et suivi.
   - C'est le module critique pour Réception → Infirmier → Médecin.
   - Ne pas modifier les statuts ou les transitions pendant l'extraction.

3. `pharmacy.py`
   - `/api/pharmacy`, stock, délivrance, mouvements, caisse.
   - Le frontend ne dépend plus des catégories de stock. Le backend conserve encore le champ historique `category` uniquement pour compatibilité Supabase : ne pas en créer de nouvelle dépendance.

4. `laboratory.py`
   - examens, résultats, prélèvements, paramètres et PDF.
   - Préserver les identifiants `patient_id`, `prescription_id`, `exam_id`, `sample_id`, `result_id`.

5. `billing.py`
   - factures, paiements, comptes patients, tarifs et lignes de compte.
   - Ne pas changer les signes comptables sans test : une dette patient ne doit pas devenir positive par erreur.

6. `maternity.py`, puis `reports.py`.

7. `doctor.py`
   - À séparer seulement après `workflow.py`, car les consultations, prescriptions et soins dépendent du workflow.

## Contrat API à préserver

- Tous les chemins `/api/...` existants doivent rester identiques.
- Les verbes HTTP, noms de paramètres et formes des réponses JSON restent identiques.
- Les frontends appellent notamment `/api/workflow/queue`, `/api/workflow/vitals`, `/api/workflow/dispatch`, `/api/medical/boxes`, `/api/pharmacy`, `/api/prescriptions`, `/api/care/prescriptions`, `/api/billing`.
- Ne pas introduire une deuxième API pour une même fonction durant la migration.
- Conserver les routes de compatibilité, par exemple `/api/care/administrations` et `/api/tariffs`, tant que les frontends les appellent.

## Changements frontend déjà réalisés

### Pharmacie

`pharmacie-app.js` traite maintenant le stock comme une liste unique de produits. Les catégories `medicament`, `injectable` et `consommable` ne doivent plus piloter le stock, la délivrance ou la recherche.

Les mouvements ne doivent pas être ajoutés deux fois : `PUT /api/pharmacy/<id>/stock` crée déjà un mouvement dans le backend. Le frontend ne poste donc plus un second mouvement après ce PUT.

### Docteur

`docteur-app.js` charge tous les éléments de `/api/pharmacy`, sans inférer ni filtrer une catégorie. Les labels UI des soins peuvent subsister, mais la source des produits est toujours la liste complète de la pharmacie.

## Problèmes backend importants connus

1. Création patient : l'erreur Supabase `created_by column ... schema cache` indique que les données envoyées ne correspondent pas au schéma réellement déployé. Corriger par compatibilité de colonnes ou migration Supabase, pas en masquant l'erreur.
2. File/dispatch : la source de vérité est `/api/workflow/queue`. Les statuts de file attendus par le workflow sont `waiting`, `with_nurse`, `vitals_done`, `assigned`, puis la clôture après consultation. Vérifier chaque transition avant toute refactorisation.
3. Temps réel : `/api/workflow/queue` ne doit pas être mis en cache côté client ou serveur pendant la coordination. `app.py` pose déjà `Cache-Control: no-store` sur cette route.
4. Stock : une opération `remove` doit refuser une quantité supérieure au stock et créer un seul mouvement.
5. Comptabilité : contrôler les montants et le signe dans `add_patient_account_line`, `create_service_invoice` et les paiements avant de modifier `billing.py`.

## Ce qu'il ne faut pas faire

- Ne pas réécrire tout `app.py` en une fois.
- Ne pas déplacer une route sans déplacer ses helpers nécessaires.
- Ne pas changer les noms de tables Supabase pendant l'extraction.
- Ne pas supprimer les routes de compatibilité sans rechercher leurs appels dans tous les fichiers frontend.
- Ne pas modifier simultanément la logique de dispatch et la structure du module `workflow.py` : extraire d'abord, corriger ensuite avec des tests.
- Ne pas réintroduire le filtrage de produits pharmacie par catégorie côté frontend.
- Ne pas stocker le dosage, la posologie ou la voie comme information obligatoire de la fiche de stock ; ils appartiennent à la prescription.

## Tests à exécuter après chaque module

```powershell
python -m py_compile backend/app.py backend/<module>.py
```

Puis démarrer l'API et vérifier au minimum :

1. `GET /api/health`.
2. Connexion via `/api/auth/login`.
3. Une route protégée avec token valide et sans token.
4. Les routes du module migré avec un rôle autorisé et un rôle non autorisé.
5. L'absence de doublon Flask : aucune exception du type « View function mapping is overwriting an existing endpoint function ».
6. Les pages frontend qui appellent le module.

## Première prochaine action

Créer `backend/patients.py`, y déplacer uniquement les routes du bloc **PATIENTS** d'`app.py`, puis enregistrer `register_patient_routes(...)` suivant le même modèle que `auth.py`.

Après cette extraction, lancer `py_compile` et tester : liste patients, création, fiche individuelle, modification, QR/barcode et accès selon rôle.
