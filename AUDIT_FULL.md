# Audit complet – Problèmes backend

## 1. Réception
| Issue | Cause probable | Impact | Priorité | Action recommandée |
|-------|----------------|--------|----------|--------------------|
| Dashboard ne fonctionne pas | Dépendance à la salle d’attente indisponible (`/api/workflow/queue` manquante) | Empêche la vue d’ensemble du trafic patient | Haute | Implémenter ou réparer la route `/api/workflow/queue` ; ajouter gestion d’erreur fallback sur le dashboard |
| Sélection du médecin impossible | Route `/api/users?role=docteur` renvoie 200 mais aucun résultat (table vide ou filtre incorrect) | Impossible de créer un RDV avec un médecin | Haute | Vérifier la logique du contrôleur, s’assurer que les médecins sont bien présents en base et que le filtre role est appliqué correctement |
| Salle d’attente non fonctionnelle | `/api/workflow/queue` retourne 404 (route inexistante) | Pas de mise en file d’attente, création de RDV échoue | Haute | Créer le contrôleur et la route, ajouter tests unitaires ; vérifier middlewares d’authentification |
| Patients hospitalisés invisibles dans "hospitalizations" | Vue utilise mauvaise route/paramètre | Les utilisateurs ne voient pas l’état d’hospitalisation | Moyenne | Auditer le service qui récupère les hospitalisations, corriger le endpoint utilisé |
| Nom du médecin manquant dans le RDV | Champ `doctorName` non renseigné ou jointure manquante | RDV incomplets, suivi difficile | Moyenne | Ajouter la jointure `JOIN doctors` dans la requête ou inclure le champ dans le DTO retourné |

## 2. Infirmier
| Issue | Cause probable | Impact | Priorité | Action recommandée |
|-------|----------------|--------|----------|--------------------|
| Salle d’attente non fonctionnelle | Même cause que la réception (`/api/workflow/queue` manquante) | Infirmiers ne peuvent pas placer les patients en attente | Haute | Réutiliser la même implémentation de la route pour les deux modules ou factoriser le service d’attente |
| Ajout patient en salle d’attente échoue (404) | Point d’entrée `/api/workflow/queue` inexistant | Blocage complet du flux d’admission | Haute | Créer la route, vérifier les autorisations requises |

## 3. Pharmacie
| Issue | Cause probable | Impact | Priorité | Action recommandée |
|-------|----------------|--------|----------|--------------------|
| Utilisation de `category` dans les médicaments | Modèle de données a été refactoré, champ supprimé mais le code reste | Incohérences, potentiels crashs | Moyenne | Nettoyer le code, supprimer les références, migrer les données si nécessaire |
| Prix des médicaments ("pris") incorrect | Typo dans le modèle (`pris` vs `price`) ou mauvaise mappage | Affichage de prix erronés, facturation incorrecte | Haute | Corriger le champ dans le schéma DB et le mapping ORM, ajouter validation côté back et front |

## 4. Laboratoire
| Issue | Cause probable | Impact | Priorité | Action recommandée |
|-------|----------------|--------|----------|--------------------|
| Formulaire de prélèvement incomplet (front ne mappe pas les champs) | Définition du formulaire UI ne correspond pas aux DTO attendus | Aucun prélèvement n’est enregistré | Haute | Redéfinir le formulaire selon les spécifications ci‑dessous (voir section "Formulaire détaillé") et mettre à jour le mapping JSON → modèle serveur |
| Vue côté médecin ne charge pas les résultats | Dépendance à `/api/workflow/queue` (404) ou problème de transformation des données | Médecins ne voient pas les résultats de laboratoire | Moyenne | Corriger la route, vérifier le service qui transforme les résultats avant de les renvoyer au front |
| `/api/laboratory/results` retourne 200 mais aucune donnée affichée | Problème de sérialisation / format de réponse (ex: renvoie `{}` au lieu d’une liste) | Absence d’affichage des résultats | Moyenne | Déboguer le contrôleur, ajouter logs, assurer que le modèle `LaboratoryResult` est correctement peuplé avant `res.json()` |

### Formulaire « Prélèvement de laboratoire » – Spécifications attendues
- **Nature du prélèvement** (enum) : Sang, Urine, Selles, Salive, Crachats, Écouvillon, Liquide céphalorachidien (LCR), Liquide pleural, Liquide synovial, Prélèvement vaginal, Prélèvement urétral, Pus, Biopsie / tissu, Autre.
- **Contenant / Support** (enum) : Tube EDTA, Tube sec, Tube citrate, Tube hépariné, Pot stérile, Flacon stérile, Écouvillon, Lame, Flacon d'hémoculture, Autre.
- **Volume (mL)** : champ texte libre (ex. 5) – optionnel selon le type de prélèvement.
- **Nombre d'échantillons** : entier (défaut 1).
- **Observations** : champ texte libre.

## 5. Docteur
| Issue | Cause probable | Impact | Priorité | Action recommandée |
|-------|----------------|--------|----------|--------------------|
| Page du docteur ne charge rien après connexion | Requête API manquante ou endpoint `/api/doctor/dashboard` absent | Interface inutilisable | Haute | Définir le endpoint, vérifier le token d’authentification, ajouter gestion d’erreur frontale |
| Dashboard du docteur ne fonctionne pas | Dépendance à `/api/workflow/queue` (404) | Visibilité du tableau de bord compromise | Haute | Réparer la route, mettre en place fallback si file d’attente indisponible |
| Salle d’attente du médecin non fonctionnelle | Même cause que précédemment (`/api/workflow/queue` 404) | Impossible de gérer les patients en attente | Haute | Centraliser la logique d’attente dans un service partagé et exposer une route unique |
| Vue laboratoire côté médecin ne charge pas les résultats | Logs 200 mais front ne reçoit de donnée (probable problème de mapping) | Médecins ne voient pas les résultats | Moyenne | Vérifier la transformation du payload côté back, ajouter tests d’intégration front/back |

## 6. Consultation / Prescription
| Issue | Cause probable | Impact | Priorité | Action recommandée |
|-------|----------------|--------|----------|--------------------|
| Ordonnance ne se transmet pas à la pharmacie | Bouton n’appelle pas l’API ou route incorrecte (`/api/prescriptions/send`) | Prescription perdue, traitement retardé | Haute | Vérifier l’événement click, implémenter l’appel POST correct, gérer la réponse succès/erreur |

## 7. Gestion des états patients
| Issue | Cause probable | Impact | Priorité | Action recommandée |
|-------|----------------|--------|----------|--------------------|
| Patients hospitalisés n’apparaissent pas dans l’onglet "soins" | Requête qui alimente l’onglet filtre uniquement les statuts "admis" ou utilise une vue obsolète | Médecins ne peuvent pas prescrire de soins aux patients hospitalisés | Haute | Mettre à jour la requête pour inclure le statut `hospitalized`, ou créer une vue combinée `patient_care_view` |
| Besoin d’afficher ces patients pour prescriptions | Même cause que ci‑dessus | Blocage du workflow clinique | Haute | Voir action précédente |

## 8. Général
| Issue | Cause probable | Impact | Priorité | Action recommandée |
|-------|----------------|--------|----------|--------------------|
| Routes `/api/workflow/queue` manquantes (404) | Endpoint non implémenté / middleware d’auth manquant | Affecte plusieurs modules (salle d’attente, dashboards, création RDV) | Critique | Implémenter le contrôleur `WorkflowQueueController`, définir les méthodes GET/POST, ajouter tests d’authentification |
| WebSocket `/ws` 404 | Route ou serveur de socket non lancé | Temps réel (notifications, tableaux de bord) indisponible | Critique | Configurer le serveur WS (ex: socket.io), créer le endpoint `/ws`, assurer que le token JWT est validé |

---
### Priorisation globale
1. **Réparer `/api/workflow/queue`** – point d’entrée central, résolution de la plupart des blocages.
2. **Corriger le WebSocket `/ws`** – restaurer le temps réel.
3. **Mettre à jour le formulaire laboratoire** – garantir la saisie correcte des prélèvements.
4. **Faire apparaître les patients hospitalisés dans l’onglet soins** – essentiel pour les prescriptions.
5. **Corriger les champs médecins et prix** – éviter les incohérences de données.
6. **Vérifier les routes de prescription et d’ordonnance** – assurer la transmission à la pharmacie.

---
> **Conclusion** : La plupart des dysfonctionnements proviennent d’une route centrale manquante (`/api/workflow/queue`) et de plusieurs incohérences entre le modèle de données et le code UI. En suivant les actions recommandées ci‑dessus et en priorisant les correctifs critiques, l’application devrait retrouver son fonctionnement complet.
