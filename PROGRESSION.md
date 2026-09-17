# I-HUB — Liste des Tâches Actives

> **Règle stricte** : Dès qu'une tâche est entièrement réalisée et validée, elle est effacée de la liste active ci-dessous.

---

## 📋 TÂCHES RESTANTES À EFFECTUER

### [ ] Étape 4 : Pharmacie — Suppression de la rigidité des catégories
- [ ] Supprimer les vérifications et filtres imposant des catégories obligatoires sur les médicaments.
- [ ] Permettre la recherche et la délivrance directes par nom ou DCI.

---

### [ ] Étape 5 : Docteur — Consultation Prénatale (CPN) & Moteur DDR
- [ ] Corriger la sauvegarde et l'affichage de la Date des Dernières Règles (DDR).
- [ ] Moteur de calcul : DDR ➔ Calcul automatique des semaines de grossesse (SA) ➔ Détermination du stade.
- [ ] Déblocage temporel : Afficher et débloquer automatiquement la CPN correspondante au terme actuel (CPN1, CPN2, CPN3, CPN4).

---

### [ ] Étape 6 : Docteur — Isolation totale Backend & Performance
- [ ] Verrouiller côté backend l'isolation stricte des rendez-vous, CPN et soins par doctor_id.
- [ ] Réaliser un audit des requêtes API au chargement du module Docteur et supprimer les ralentissements/doublons.

---

## ✅ ÉTAPES TERMINÉES ET ARCHIVÉES
- [x] **Étape 2 & 3 : Infirmier & Docteur — Moteur de Planification Progressive des Soins & Dashboard** :
  - Sélecteurs en dur (heures de 1 à 24 et jours de 1 à 7) pour zéro erreur de calcul.
  - Calcul mathématique automatique du nombre total d'occurrences (jours × 24) ÷ heures côté frontend et backend.
  - Affichage progressif d'une seule occurrence active à la fois (Soin 1/N).
  - Horodatage et enregistrement de chaque geste avec nom de l'infirmier, avancement automatique de l'occurrence, calcul de la prochaine échéance et compte à rebours dynamique (« Prochain soin dans X h »).
  - Clôture automatique du protocole après la dernière occurrence (bascule en terminé dans l'historique).
  - Dashboard infirmier : bloc « Soins à effectuer » connecté en temps réel aux soins actifs du jour.
  - Présentation infirmerie : regroupement des soins par médecin prescripteur en en-tête groupé unique.
  - Côté Docteur : bouton neutre discret [ Suivi des soins ] apparaissant uniquement si le patient a des soins actifs, ouvrant le carnet de soins en temps réel (progression, passages infirmiers, prochaine prise).
- [x] **Étape 1 : Réception — Salle d'attente propre et temps réel** : Gestion automatique après minuit côté backend (tous les patients non clôturés des jours précédents passent à completed/sortie), archivage des 19 anciens tests résiduels dans Supabase, aucun filtre visuel de date côté frontend, et action unique « Fiche » dans le tableau.
- [x] **Socle initial** : Devise FC en pharmacie, Formulaire de prélèvement labo (14 natures, 10 contenants), Attribution du Box 1 à medecin@gmail.com (ID 106), correction du crash 500 sur les hospitalisations, harmonisation du total patients, et filtrage strict réception sur waiting.
