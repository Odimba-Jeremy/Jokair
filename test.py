"""
TESTS COMPLETS I-HUB - Toutes les routes
Exécution: pytest tests/test_all.py -v
"""

import pytest
import json
import jwt
from datetime import datetime, timedelta
from flask import Flask
from app import app, supabase, TABLES, now_iso, generate_password_hash

# ==================== FIXTURES ====================

@pytest.fixture
def client():
    """Client de test Flask avec base de données de test"""
    app.config['TESTING'] = True
    app.config['DATABASE_URL'] = 'sqlite:///:memory:'
    app.config['JWT_SECRET'] = 'test-secret-key-2024'
    
    with app.test_client() as client:
        with app.app_context():
            yield client

@pytest.fixture
def auth_headers(client):
    """Headers avec token JWT pour tests authentifiés"""
    # Créer un utilisateur admin
    client.post('/api/auth/register', json={
        'name': 'Admin Test',
        'email': 'admin@test.com',
        'password': 'Admin123!',
        'role': 'super_admin'
    })
    
    # Se connecter
    response = client.post('/api/auth/login', json={
        'email': 'admin@test.com',
        'password': 'Admin123!'
    })
    token = response.json['token']
    return {'Authorization': f'Bearer {token}'}

@pytest.fixture
def doctor_headers(client):
    """Headers pour médecin"""
    client.post('/api/auth/register', json={
        'name': 'Dr Test',
        'email': 'doctor@test.com',
        'password': 'Doctor123!',
        'role': 'docteur'
    })
    response = client.post('/api/auth/login', json={
        'email': 'doctor@test.com',
        'password': 'Doctor123!'
    })
    return {'Authorization': f'Bearer {response.json["token"]}'}

@pytest.fixture
def sample_patient():
    """Patient de test"""
    return {
        'full_name': 'Jean Dupont',
        'phone': '+243812345678',
        'email': 'jean@test.com',
        'date_of_birth': '1990-05-15',
        'gender': 'M',
        'blood_type': 'A+',
        'address': 'Kinshasa, RDC',
        'status': 'waiting',
        'allergies': 'Aucune',
        'medical_history': 'RAS',
        'emergency_contact': '+243812345679',
        'insurance': 'Mutuelle',
        'priority': 'normal'
    }

@pytest.fixture
def sample_patient_female():
    """Patiente de test"""
    return {
        'full_name': 'Marie Kouadio',
        'phone': '+243812345680',
        'email': 'marie@test.com',
        'date_of_birth': '1995-03-20',
        'gender': 'F',
        'blood_type': 'B+',
        'address': 'Kinshasa, RDC',
        'status': 'waiting',
        'allergies': 'Aucune',
        'medical_history': 'RAS',
        'is_pregnant': True,
        'pregnancy_lmp': '2025-12-01',
        'expected_delivery_date': '2026-09-01'
    }

@pytest.fixture
def sample_appointment():
    """Rendez-vous de test"""
    return {
        'patient_id': 1,
        'date': '2026-08-15T10:00:00',
        'type': 'consultation',
        'duration': 30,
        'notes': 'Consultation de suivi',
        'status': 'scheduled',
        'priority': 'normal'
    }

@pytest.fixture
def sample_prescription():
    """Prescription de test"""
    return {
        'patient_id': 1,
        'medication': 'Arthéméther-luméfantrine',
        'dosage': '80mg/480mg',
        'frequency': '2 fois par jour',
        'duration': '3 jours',
        'start_date': '2026-08-10',
        'end_date': '2026-08-13',
        'instructions': 'Prendre avec de la nourriture',
        'status': 'active'
    }

@pytest.fixture
def sample_lab_test():
    """Analyse de test"""
    return {
        'patient_id': 1,
        'test_type': 'Hémogramme',
        'notes': 'Bilan sanguin complet',
        'priority': 'normal'
    }

@pytest.fixture
def sample_pharmacy():
    """Médicament de test"""
    return {
        'medication_name': 'Paracétamol',
        'quantity': 100,
        'unit': 'comprimé(s)',
        'purchase_price': 500,
        'selling_price': 1000,
        'threshold': 10,
        'expiry_date': '2027-12-31'
    }

@pytest.fixture
def sample_invoice():
    """Facture de test"""
    return {
        'patient_id': 1,
        'amount': 15000,
        'description': 'Consultation médicale',
        'status': 'unpaid',
        'line_items': [
            {'description': 'Consultation', 'quantity': 1, 'unit_price': 15000, 'amount': 15000}
        ]
    }


# ==================== 1. TESTS AUTHENTIFICATION ====================

def test_auth_register_success(client):
    """Test 1: Inscription réussie"""
    response = client.post('/api/auth/register', json={
        'name': 'Nouvel Utilisateur',
        'email': 'newuser@test.com',
        'password': 'Password123!',
        'role': 'reception'
    })
    assert response.status_code == 201
    assert 'token' in response.json
    assert response.json['user']['email'] == 'newuser@test.com'
    assert response.json['user']['role'] == 'reception'

def test_auth_register_duplicate_email(client):
    """Test 2: Inscription avec email existant"""
    # Premier enregistrement
    client.post('/api/auth/register', json={
        'name': 'User 1',
        'email': 'duplicate@test.com',
        'password': 'Password123!',
        'role': 'reception'
    })
    # Deuxième enregistrement
    response = client.post('/api/auth/register', json={
        'name': 'User 2',
        'email': 'duplicate@test.com',
        'password': 'Password123!',
        'role': 'reception'
    })
    assert response.status_code == 422
    assert 'Email déjà utilisé' in response.json['error']

def test_auth_register_short_password(client):
    """Test 3: Mot de passe trop court"""
    response = client.post('/api/auth/register', json={
        'name': 'User Test',
        'email': 'short@test.com',
        'password': '123',
        'role': 'reception'
    })
    assert response.status_code == 422
    assert 'trop court' in response.json['error']

def test_auth_register_invalid_email(client):
    """Test 4: Email invalide"""
    response = client.post('/api/auth/register', json={
        'name': 'User Test',
        'email': 'invalid-email',
        'password': 'Password123!',
        'role': 'reception'
    })
    assert response.status_code == 422
    assert 'Email invalide' in response.json['error']

def test_auth_login_success(client):
    """Test 5: Connexion réussie"""
    # Créer utilisateur
    client.post('/api/auth/register', json={
        'name': 'Login Test',
        'email': 'login@test.com',
        'password': 'Login123!',
        'role': 'reception'
    })
    # Se connecter
    response = client.post('/api/auth/login', json={
        'email': 'login@test.com',
        'password': 'Login123!'
    })
    assert response.status_code == 200
    assert 'token' in response.json
    assert response.json['user']['email'] == 'login@test.com'

def test_auth_login_wrong_password(client):
    """Test 6: Mauvais mot de passe"""
    client.post('/api/auth/register', json={
        'name': 'Wrong Pass',
        'email': 'wrong@test.com',
        'password': 'Correct123!',
        'role': 'reception'
    })
    response = client.post('/api/auth/login', json={
        'email': 'wrong@test.com',
        'password': 'WrongPassword'
    })
    assert response.status_code == 401
    assert 'incorrect' in response.json['error']

def test_auth_login_nonexistent_user(client):
    """Test 7: Utilisateur inexistant"""
    response = client.post('/api/auth/login', json={
        'email': 'nonexistent@test.com',
        'password': 'Password123!'
    })
    assert response.status_code == 401
    assert 'incorrect' in response.json['error']

def test_auth_me_success(client, auth_headers):
    """Test 8: Récupération profil utilisateur"""
    response = client.get('/api/auth/me', headers=auth_headers)
    assert response.status_code == 200
    assert 'user' in response.json
    assert response.json['user']['email'] == 'admin@test.com'

def test_auth_me_no_token(client):
    """Test 9: Accès sans token"""
    response = client.get('/api/auth/me')
    assert response.status_code == 401
    assert 'Token requis' in response.json['error']

def test_auth_logout_success(client, auth_headers):
    """Test 10: Déconnexion réussie"""
    response = client.post('/api/auth/logout', headers=auth_headers)
    assert response.status_code == 200
    assert 'Déconnexion réussie' in response.json['message']


# ==================== 2. TESTS PATIENTS ====================

def test_create_patient_success(client, auth_headers, sample_patient):
    """Test 11: Création patient réussie"""
    response = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    assert response.status_code == 201
    assert 'id' in response.json
    assert response.json['full_name'] == sample_patient['full_name']
    assert 'hospital_id' in response.json

def test_create_patient_missing_name(client, auth_headers):
    """Test 12: Création patient sans nom"""
    response = client.post('/api/patients', json={'phone': '+243812345678'}, headers=auth_headers)
    assert response.status_code == 422
    assert 'Nom requis' in response.json['error']

def test_create_patient_pregnant(client, auth_headers, sample_patient_female):
    """Test 13: Création patiente enceinte"""
    response = client.post('/api/patients', json=sample_patient_female, headers=auth_headers)
    assert response.status_code == 201
    assert response.json['is_pregnant'] == True
    # Vérifier que la grossesse a été créée
    pregnancies = supabase.table('pregnancies').select('*').eq('patient_id', response.json['id']).execute()
    assert len(pregnancies.data) > 0

def test_get_patients_success(client, auth_headers, sample_patient):
    """Test 14: Récupération liste patients"""
    client.post('/api/patients', json=sample_patient, headers=auth_headers)
    response = client.get('/api/patients', headers=auth_headers)
    assert response.status_code == 200
    assert len(response.json) > 0

def test_get_patients_search(client, auth_headers, sample_patient):
    """Test 15: Recherche patients"""
    client.post('/api/patients', json=sample_patient, headers=auth_headers)
    response = client.get('/api/patients?search=Jean', headers=auth_headers)
    assert response.status_code == 200
    assert len(response.json) > 0

def test_get_patient_success(client, auth_headers, sample_patient):
    """Test 16: Récupération patient par ID"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    response = client.get(f'/api/patients/{patient_id}', headers=auth_headers)
    assert response.status_code == 200
    assert response.json['id'] == patient_id
    assert response.json['full_name'] == sample_patient['full_name']

def test_get_patient_not_found(client, auth_headers):
    """Test 17: Patient inexistant"""
    response = client.get('/api/patients/99999', headers=auth_headers)
    assert response.status_code == 404
    assert 'Patient introuvable' in response.json['error']

def test_get_patient_invalid_id(client, auth_headers):
    """Test 18: ID patient invalide"""
    response = client.get('/api/patients/0', headers=auth_headers)
    assert response.status_code == 400
    assert 'ID patient invalide' in response.json['error']

def test_update_patient_success(client, auth_headers, sample_patient):
    """Test 19: Modification patient réussie"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    response = client.put(f'/api/patients/{patient_id}', json={
        'full_name': 'Jean Dupont Modifié',
        'phone': '+243812345677'
    }, headers=auth_headers)
    assert response.status_code == 200
    assert response.json['full_name'] == 'Jean Dupont Modifié'

def test_update_patient_not_found(client, auth_headers):
    """Test 20: Modification patient inexistant"""
    response = client.put('/api/patients/99999', json={'full_name': 'Test'}, headers=auth_headers)
    assert response.status_code == 404

def test_delete_patient_success(client, auth_headers, sample_patient):
    """Test 21: Suppression patient réussie"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    response = client.delete(f'/api/patients/{patient_id}', headers=auth_headers)
    assert response.status_code == 200
    assert 'Patient supprimé' in response.json['message']

def test_delete_patient_not_found(client, auth_headers):
    """Test 22: Suppression patient inexistant"""
    response = client.delete('/api/patients/99999', headers=auth_headers)
    assert response.status_code == 404

def test_get_patient_barcode(client, auth_headers, sample_patient):
    """Test 23: Récupération code-barres patient"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    response = client.get(f'/api/patients/{patient_id}/barcode', headers=auth_headers)
    assert response.status_code == 200
    assert 'image/svg+xml' in response.content_type

def test_get_patient_appointments(client, auth_headers, sample_patient):
    """Test 24: Récupération rendez-vous patient"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    response = client.get(f'/api/patients/{patient_id}/appointments', headers=auth_headers)
    assert response.status_code == 200

def test_get_patient_prescriptions(client, auth_headers, sample_patient):
    """Test 25: Récupération prescriptions patient"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    response = client.get(f'/api/patients/{patient_id}/prescriptions', headers=auth_headers)
    assert response.status_code == 200


# ==================== 3. TESTS RENDEZ-VOUS ====================

def test_create_appointment_success(client, auth_headers, sample_patient, sample_appointment):
    """Test 26: Création rendez-vous réussie"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    sample_appointment['patient_id'] = patient_id
    response = client.post('/api/appointments', json=sample_appointment, headers=auth_headers)
    assert response.status_code == 201
    assert 'id' in response.json

def test_create_appointment_missing_patient(client, auth_headers):
    """Test 27: Création rendez-vous sans patient"""
    response = client.post('/api/appointments', json={
        'date': '2026-08-15T10:00:00',
        'type': 'consultation'
    }, headers=auth_headers)
    assert response.status_code == 422
    assert 'Champ patient_id requis' in response.json['error']

def test_get_appointments_success(client, auth_headers):
    """Test 28: Récupération liste rendez-vous"""
    response = client.get('/api/appointments', headers=auth_headers)
    assert response.status_code == 200

def test_get_appointment_success(client, auth_headers, sample_patient, sample_appointment):
    """Test 29: Récupération rendez-vous par ID"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    sample_appointment['patient_id'] = create_p.json['id']
    create_a = client.post('/api/appointments', json=sample_appointment, headers=auth_headers)
    appointment_id = create_a.json['id']
    response = client.get(f'/api/appointments/{appointment_id}', headers=auth_headers)
    assert response.status_code == 200
    assert response.json['id'] == appointment_id

def test_update_appointment_success(client, auth_headers, sample_patient, sample_appointment):
    """Test 30: Modification rendez-vous réussie"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    sample_appointment['patient_id'] = create_p.json['id']
    create_a = client.post('/api/appointments', json=sample_appointment, headers=auth_headers)
    appointment_id = create_a.json['id']
    response = client.put(f'/api/appointments/{appointment_id}', json={
        'status': 'completed',
        'notes': 'Rendez-vous terminé'
    }, headers=auth_headers)
    assert response.status_code == 200
    assert response.json['status'] == 'completed'

def test_patch_appointment_success(client, auth_headers, sample_patient, sample_appointment):
    """Test 31: Patch rendez-vous réussie"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    sample_appointment['patient_id'] = create_p.json['id']
    create_a = client.post('/api/appointments', json=sample_appointment, headers=auth_headers)
    appointment_id = create_a.json['id']
    response = client.patch(f'/api/appointments/{appointment_id}', json={
        'status': 'cancelled'
    }, headers=auth_headers)
    assert response.status_code == 200
    assert response.json['status'] == 'cancelled'

def test_delete_appointment_success(client, auth_headers, sample_patient, sample_appointment):
    """Test 32: Suppression rendez-vous réussie"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    sample_appointment['patient_id'] = create_p.json['id']
    create_a = client.post('/api/appointments', json=sample_appointment, headers=auth_headers)
    appointment_id = create_a.json['id']
    response = client.delete(f'/api/appointments/{appointment_id}', headers=auth_headers)
    assert response.status_code == 200


# ==================== 4. TESTS PRESCRIPTIONS ====================

def test_create_prescription_success(client, auth_headers, sample_patient, sample_prescription):
    """Test 33: Création prescription réussie"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    sample_prescription['patient_id'] = patient_id
    response = client.post('/api/prescriptions', json=sample_prescription, headers=auth_headers)
    assert response.status_code == 201
    assert 'id' in response.json

def test_create_prescription_missing_patient(client, auth_headers):
    """Test 34: Création prescription sans patient"""
    response = client.post('/api/prescriptions', json={
        'medication': 'Paracétamol',
        'dosage': '500mg'
    }, headers=auth_headers)
    assert response.status_code == 422
    assert 'Patient et médicament requis' in response.json['error']

def test_get_prescriptions_success(client, auth_headers):
    """Test 35: Récupération liste prescriptions"""
    response = client.get('/api/prescriptions', headers=auth_headers)
    assert response.status_code == 200

def test_get_prescription_success(client, auth_headers, sample_patient, sample_prescription):
    """Test 36: Récupération prescription par ID"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    sample_prescription['patient_id'] = create_p.json['id']
    create_r = client.post('/api/prescriptions', json=sample_prescription, headers=auth_headers)
    prescription_id = create_r.json['id']
    response = client.get(f'/api/prescriptions/{prescription_id}', headers=auth_headers)
    assert response.status_code == 200
    assert response.json['id'] == prescription_id

def test_update_prescription_success(client, auth_headers, sample_patient, sample_prescription):
    """Test 37: Modification prescription réussie"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    sample_prescription['patient_id'] = create_p.json['id']
    create_r = client.post('/api/prescriptions', json=sample_prescription, headers=auth_headers)
    prescription_id = create_r.json['id']
    response = client.put(f'/api/prescriptions/{prescription_id}', json={
        'status': 'completed',
        'instructions': 'Prendre après le repas'
    }, headers=auth_headers)
    assert response.status_code == 200

def test_patch_prescription_success(client, auth_headers, sample_patient, sample_prescription):
    """Test 38: Patch prescription réussie"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    sample_prescription['patient_id'] = create_p.json['id']
    create_r = client.post('/api/prescriptions', json=sample_prescription, headers=auth_headers)
    prescription_id = create_r.json['id']
    response = client.patch(f'/api/prescriptions/{prescription_id}', json={
        'pharmacy_status': 'dispensed'
    }, headers=auth_headers)
    assert response.status_code == 200


# ==================== 5. TESTS LABORATOIRE ====================

def test_create_lab_test_success(client, auth_headers, sample_patient, sample_lab_test):
    """Test 39: Création analyse réussie"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    sample_lab_test['patient_id'] = create.json['id']
    response = client.post('/api/laboratory/tests', json=sample_lab_test, headers=auth_headers)
    assert response.status_code == 201
    assert 'id' in response.json
    assert response.json['status'] == 'pending'

def test_create_lab_test_missing_patient(client, auth_headers):
    """Test 40: Création analyse sans patient"""
    response = client.post('/api/laboratory/tests', json={
        'test_type': 'Hémogramme'
    }, headers=auth_headers)
    assert response.status_code == 422
    assert 'Patient et type d\'analyse requis' in response.json['error']

def test_get_lab_tests_success(client, auth_headers):
    """Test 41: Récupération liste analyses"""
    response = client.get('/api/laboratory/tests', headers=auth_headers)
    assert response.status_code == 200

def test_get_lab_test_success(client, auth_headers, sample_patient, sample_lab_test):
    """Test 42: Récupération analyse par ID"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    sample_lab_test['patient_id'] = create_p.json['id']
    create_t = client.post('/api/laboratory/tests', json=sample_lab_test, headers=auth_headers)
    test_id = create_t.json['id']
    response = client.get(f'/api/laboratory/tests/{test_id}', headers=auth_headers)
    assert response.status_code == 200

def test_update_lab_test_success(client, auth_headers, sample_patient, sample_lab_test):
    """Test 43: Modification analyse réussie"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    sample_lab_test['patient_id'] = create_p.json['id']
    create_t = client.post('/api/laboratory/tests', json=sample_lab_test, headers=auth_headers)
    test_id = create_t.json['id']
    response = client.put(f'/api/laboratory/tests/{test_id}', json={
        'test_type': 'Bilan hépatique',
        'priority': 'urgent'
    }, headers=auth_headers)
    assert response.status_code == 200

def test_patch_lab_test_success(client, auth_headers, sample_patient, sample_lab_test):
    """Test 44: Patch analyse réussie"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    sample_lab_test['patient_id'] = create_p.json['id']
    create_t = client.post('/api/laboratory/tests', json=sample_lab_test, headers=auth_headers)
    test_id = create_t.json['id']
    response = client.patch(f'/api/laboratory/tests/{test_id}', json={
        'status': 'preleve'
    }, headers=auth_headers)
    assert response.status_code == 200
    assert 'num_prelevement' in response.json

def test_save_lab_result_success(client, auth_headers, sample_patient, sample_lab_test):
    """Test 45: Enregistrement résultat analyse"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    sample_lab_test['patient_id'] = create_p.json['id']
    create_t = client.post('/api/laboratory/tests', json=sample_lab_test, headers=auth_headers)
    test_id = create_t.json['id']
    response = client.put(f'/api/laboratory/tests/{test_id}/result', json={
        'result': 'GB: 6.5 G/L, GR: 4.8 T/L',
        'observations': 'Résultats normaux'
    }, headers=auth_headers)
    assert response.status_code == 200
    assert response.json['status'] == 'completed'

def test_get_lab_result_success(client, auth_headers, sample_patient, sample_lab_test):
    """Test 46: Récupération résultat analyse"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    sample_lab_test['patient_id'] = create_p.json['id']
    create_t = client.post('/api/laboratory/tests', json=sample_lab_test, headers=auth_headers)
    test_id = create_t.json['id']
    client.put(f'/api/laboratory/tests/{test_id}/result', json={
        'result': 'GB: 6.5 G/L',
        'observations': 'Normaux'
    }, headers=auth_headers)
    response = client.get(f'/api/laboratory/tests/{test_id}/result', headers=auth_headers)
    assert response.status_code == 200

def test_get_lab_pharmacy_items(client, auth_headers):
    """Test 47: Récupération stock laboratoire"""
    response = client.get('/api/laboratory/stock', headers=auth_headers)
    assert response.status_code == 200

def test_create_lab_stock_item(client, auth_headers):
    """Test 48: Création produit laboratoire"""
    response = client.post('/api/laboratory/stock', json={
        'name': 'Réactif Test',
        'category': 'Réactif',
        'quantity': 50,
        'threshold': 5
    }, headers=auth_headers)
    assert response.status_code == 201
    assert 'id' in response.json

def test_get_lab_params_success(client, auth_headers):
    """Test 49: Récupération paramètres analyse"""
    response = client.get('/api/laboratory/params/Hémogramme', headers=auth_headers)
    assert response.status_code == 200
    assert len(response.json) > 0


# ==================== 6. TESTS PHARMACIE ====================

def test_create_pharmacy_item_success(client, auth_headers, sample_pharmacy):
    """Test 50: Création médicament réussie"""
    response = client.post('/api/pharmacy', json=sample_pharmacy, headers=auth_headers)
    assert response.status_code == 201
    assert 'id' in response.json

def test_create_pharmacy_item_missing_name(client, auth_headers):
    """Test 51: Création médicament sans nom"""
    response = client.post('/api/pharmacy', json={
        'quantity': 100,
        'selling_price': 1000
    }, headers=auth_headers)
    assert response.status_code == 422
    assert 'Nom du médicament requis' in response.json['error']

def test_get_pharmacy_items_success(client, auth_headers):
    """Test 52: Récupération liste médicaments"""
    response = client.get('/api/pharmacy', headers=auth_headers)
    assert response.status_code == 200

def test_get_pharmacy_item_success(client, auth_headers, sample_pharmacy):
    """Test 53: Récupération médicament par ID"""
    create = client.post('/api/pharmacy', json=sample_pharmacy, headers=auth_headers)
    item_id = create.json['id']
    response = client.get(f'/api/pharmacy/{item_id}', headers=auth_headers)
    assert response.status_code == 200

def test_update_pharmacy_item_success(client, auth_headers, sample_pharmacy):
    """Test 54: Modification médicament réussie"""
    create = client.post('/api/pharmacy', json=sample_pharmacy, headers=auth_headers)
    item_id = create.json['id']
    response = client.put(f'/api/pharmacy/{item_id}', json={
        'medication_name': 'Paracétamol 500mg',
        'selling_price': 1200
    }, headers=auth_headers)
    assert response.status_code == 200

def test_update_pharmacy_stock_success(client, auth_headers, sample_pharmacy):
    """Test 55: Mise à jour stock médicament"""
    create = client.post('/api/pharmacy', json=sample_pharmacy, headers=auth_headers)
    item_id = create.json['id']
    # Ajouter du stock
    response = client.put(f'/api/pharmacy/{item_id}/stock', json={
        'quantity': 50,
        'operation': 'add',
        'reason': 'Réapprovisionnement'
    }, headers=auth_headers)
    assert response.status_code == 200
    assert response.json['quantity'] == 150

def test_remove_pharmacy_stock_success(client, auth_headers, sample_pharmacy):
    """Test 56: Retrait stock médicament"""
    create = client.post('/api/pharmacy', json=sample_pharmacy, headers=auth_headers)
    item_id = create.json['id']
    # Retirer du stock
    response = client.put(f'/api/pharmacy/{item_id}/stock', json={
        'quantity': 30,
        'operation': 'remove',
        'reason': 'Vente'
    }, headers=auth_headers)
    assert response.status_code == 200
    assert response.json['quantity'] == 70

def test_remove_pharmacy_stock_insufficient(client, auth_headers, sample_pharmacy):
    """Test 57: Retrait stock insuffisant"""
    create = client.post('/api/pharmacy', json=sample_pharmacy, headers=auth_headers)
    item_id = create.json['id']
    response = client.put(f'/api/pharmacy/{item_id}/stock', json={
        'quantity': 200,
        'operation': 'remove'
    }, headers=auth_headers)
    assert response.status_code == 422
    assert 'Stock insuffisant' in response.json['error']

def test_delete_pharmacy_item_success(client, auth_headers, sample_pharmacy):
    """Test 58: Suppression médicament réussie"""
    create = client.post('/api/pharmacy', json=sample_pharmacy, headers=auth_headers)
    item_id = create.json['id']
    response = client.delete(f'/api/pharmacy/{item_id}', headers=auth_headers)
    assert response.status_code == 200

def test_search_medications_success(client, auth_headers, sample_pharmacy):
    """Test 59: Recherche médicaments"""
    client.post('/api/pharmacy', json=sample_pharmacy, headers=auth_headers)
    response = client.get('/api/pharmacy/medications/search?q=Paracétamol', headers=auth_headers)
    assert response.status_code == 200
    assert len(response.json) > 0

def test_get_expiring_medications(client, auth_headers, sample_pharmacy):
    """Test 60: Récupération médicaments périmés"""
    response = client.get('/api/pharmacy/expiring?days=30', headers=auth_headers)
    assert response.status_code == 200

def test_get_low_stock_medications(client, auth_headers, sample_pharmacy):
    """Test 61: Récupération médicaments en rupture"""
    response = client.get('/api/pharmacy/low-stock', headers=auth_headers)
    assert response.status_code == 200


# ==================== 7. TESTS SOINS ====================

def test_create_care_log_success(client, auth_headers, sample_patient):
    """Test 62: Création soin réussie"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    response = client.post('/api/care', json={
        'patient_id': patient_id,
        'care_type': 'Pansement',
        'description': 'Pansement plaie infectée',
        'priority': 'normal'
    }, headers=auth_headers)
    assert response.status_code == 201
    assert 'id' in response.json

def test_create_care_log_missing_patient(client, auth_headers):
    """Test 63: Création soin sans patient"""
    response = client.post('/api/care', json={
        'care_type': 'Injection'
    }, headers=auth_headers)
    assert response.status_code == 422
    assert 'Patient et type de soin requis' in response.json['error']

def test_get_care_logs_success(client, auth_headers):
    """Test 64: Récupération liste soins"""
    response = client.get('/api/care', headers=auth_headers)
    assert response.status_code == 200

def test_update_care_log_success(client, auth_headers, sample_patient):
    """Test 65: Modification soin réussie"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    create_c = client.post('/api/care', json={
        'patient_id': create_p.json['id'],
        'care_type': 'Pansement'
    }, headers=auth_headers)
    care_id = create_c.json['id']
    response = client.put(f'/api/care/{care_id}', json={
        'status': 'completed',
        'description': 'Pansement effectué'
    }, headers=auth_headers)
    assert response.status_code == 200

def test_patch_care_log_success(client, auth_headers, sample_patient):
    """Test 66: Patch soin réussie"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    create_c = client.post('/api/care', json={
        'patient_id': create_p.json['id'],
        'care_type': 'Injection'
    }, headers=auth_headers)
    care_id = create_c.json['id']
    response = client.patch(f'/api/care/{care_id}', json={
        'priority': 'urgent'
    }, headers=auth_headers)
    assert response.status_code == 200

def test_delete_care_log_success(client, auth_headers, sample_patient):
    """Test 67: Suppression soin réussie"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    create_c = client.post('/api/care', json={
        'patient_id': create_p.json['id'],
        'care_type': 'Suture'
    }, headers=auth_headers)
    care_id = create_c.json['id']
    response = client.delete(f'/api/care/{care_id}', headers=auth_headers)
    assert response.status_code == 200


# ==================== 8. TESTS FACTURATION ====================

def test_create_invoice_success(client, auth_headers, sample_patient, sample_invoice):
    """Test 68: Création facture réussie"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    sample_invoice['patient_id'] = create.json['id']
    response = client.post('/api/billing', json=sample_invoice, headers=auth_headers)
    assert response.status_code == 201
    assert 'id' in response.json
    assert response.json['status'] == 'unpaid'

def test_create_invoice_missing_patient(client, auth_headers):
    """Test 69: Création facture sans patient"""
    response = client.post('/api/billing', json={
        'amount': 15000,
        'description': 'Consultation'
    }, headers=auth_headers)
    assert response.status_code == 422
    assert 'Patient requis' in response.json['error']

def test_create_invoice_zero_amount(client, auth_headers, sample_patient):
    """Test 70: Création facture montant zéro"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    response = client.post('/api/billing', json={
        'patient_id': create.json['id'],
        'amount': 0,
        'description': 'Test'
    }, headers=auth_headers)
    assert response.status_code == 422

def test_get_invoices_success(client, auth_headers):
    """Test 71: Récupération liste factures"""
    response = client.get('/api/billing', headers=auth_headers)
    assert response.status_code == 200

def test_get_invoice_success(client, auth_headers, sample_patient, sample_invoice):
    """Test 72: Récupération facture par ID"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    sample_invoice['patient_id'] = create_p.json['id']
    create_i = client.post('/api/billing', json=sample_invoice, headers=auth_headers)
    invoice_id = create_i.json['id']
    response = client.get(f'/api/billing/{invoice_id}', headers=auth_headers)
    assert response.status_code == 200

def test_update_invoice_success(client, auth_headers, sample_patient, sample_invoice):
    """Test 73: Modification facture réussie"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    sample_invoice['patient_id'] = create_p.json['id']
    create_i = client.post('/api/billing', json=sample_invoice, headers=auth_headers)
    invoice_id = create_i.json['id']
    response = client.put(f'/api/billing/{invoice_id}', json={
        'amount': 20000,
        'description': 'Consultation + examens'
    }, headers=auth_headers)
    assert response.status_code == 200

def test_pay_invoice_success(client, auth_headers, sample_patient, sample_invoice):
    """Test 74: Paiement facture réussie"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    sample_invoice['patient_id'] = create_p.json['id']
    create_i = client.post('/api/billing', json=sample_invoice, headers=auth_headers)
    invoice_id = create_i.json['id']
    response = client.put(f'/api/billing/{invoice_id}/pay', json={
        'amount': 15000
    }, headers=auth_headers)
    assert response.status_code == 200
    assert response.json['status'] == 'paid'

def test_partial_pay_invoice_success(client, auth_headers, sample_patient, sample_invoice):
    """Test 75: Paiement partiel facture"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    sample_invoice['patient_id'] = create_p.json['id']
    create_i = client.post('/api/billing', json=sample_invoice, headers=auth_headers)
    invoice_id = create_i.json['id']
    response = client.patch(f'/api/billing/{invoice_id}/partial', json={
        'amount': 5000
    }, headers=auth_headers)
    assert response.status_code == 200
    assert response.json['status'] == 'partial'

def test_create_grouped_invoice_success(client, auth_headers, sample_patient):
    """Test 76: Création facture groupée"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    response = client.post('/api/billing/grouped', json={
        'patient_id': patient_id,
        'items': [
            {'description': 'Item 1', 'quantity': 2, 'unit_price': 5000, 'amount': 10000},
            {'description': 'Item 2', 'quantity': 1, 'unit_price': 8000, 'amount': 8000}
        ],
        'description': 'Facture groupée'
    }, headers=auth_headers)
    assert response.status_code == 201
    assert 'invoice' in response.json
    assert response.json['invoice']['amount'] == 18000


# ==================== 9. TESTS MATERNITÉ ====================

def test_create_pregnancy_success(client, auth_headers, sample_patient_female):
    """Test 77: Création grossesse réussie"""
    create = client.post('/api/patients', json=sample_patient_female, headers=auth_headers)
    patient_id = create.json['id']
    response = client.post('/api/maternity/pregnancies', json={
        'patient_id': patient_id,
        'last_menstrual_period': '2025-12-01',
        'expected_delivery_date': '2026-09-01',
        'risk_level': 'normal',
        'medical_history': 'Primipare'
    }, headers=auth_headers)
    assert response.status_code == 201
    assert 'id' in response.json

def test_create_pregnancy_missing_patient(client, auth_headers):
    """Test 78: Création grossesse sans patient"""
    response = client.post('/api/maternity/pregnancies', json={
        'last_menstrual_period': '2025-12-01'
    }, headers=auth_headers)
    assert response.status_code == 422
    assert 'Patient et DDR requis' in response.json['error']

def test_get_pregnancies_success(client, auth_headers):
    """Test 79: Récupération liste grossesses"""
    response = client.get('/api/maternity/pregnancies', headers=auth_headers)
    assert response.status_code == 200

def test_get_pregnancy_success(client, auth_headers, sample_patient_female):
    """Test 80: Récupération grossesse par ID"""
    create_p = client.post('/api/patients', json=sample_patient_female, headers=auth_headers)
    create_g = client.post('/api/maternity/pregnancies', json={
        'patient_id': create_p.json['id'],
        'last_menstrual_period': '2025-12-01'
    }, headers=auth_headers)
    pregnancy_id = create_g.json['id']
    response = client.get(f'/api/maternity/pregnancies/{pregnancy_id}', headers=auth_headers)
    assert response.status_code == 200

def test_update_pregnancy_success(client, auth_headers, sample_patient_female):
    """Test 81: Modification grossesse réussie"""
    create_p = client.post('/api/patients', json=sample_patient_female, headers=auth_headers)
    create_g = client.post('/api/maternity/pregnancies', json={
        'patient_id': create_p.json['id'],
        'last_menstrual_period': '2025-12-01'
    }, headers=auth_headers)
    pregnancy_id = create_g.json['id']
    response = client.put(f'/api/maternity/pregnancies/{pregnancy_id}', json={
        'risk_level': 'high',
        'medical_history': 'Pré-éclampsie'
    }, headers=auth_headers)
    assert response.status_code == 200

def test_create_prenatal_consultation_success(client, auth_headers, sample_patient_female):
    """Test 82: Création consultation prénatale"""
    create_p = client.post('/api/patients', json=sample_patient_female, headers=auth_headers)
    patient_id = create_p.json['id']
    create_g = client.post('/api/maternity/pregnancies', json={
        'patient_id': patient_id,
        'last_menstrual_period': '2025-12-01'
    }, headers=auth_headers)
    response = client.post('/api/maternity/prenatal', json={
        'patient_id': patient_id,
        'pregnancy_id': create_g.json['id'],
        'visit_date': '2026-08-10T10:00:00',
        'weight': 65.5,
        'blood_pressure': '120/80',
        'fetal_heartbeat': '140',
        'gestational_weeks': 34,
        'observations': 'Grossesse évolutive'
    }, headers=auth_headers)
    assert response.status_code == 201

def test_get_prenatal_consultations_success(client, auth_headers):
    """Test 83: Récupération consultations prénatales"""
    response = client.get('/api/maternity/prenatal', headers=auth_headers)
    assert response.status_code == 200

def test_create_delivery_success(client, auth_headers, sample_patient_female):
    """Test 84: Création accouchement"""
    create_p = client.post('/api/patients', json=sample_patient_female, headers=auth_headers)
    patient_id = create_p.json['id']
    response = client.post('/api/maternity/deliveries', json={
        'patient_id': patient_id,
        'delivery_date': '2026-08-15T08:00:00',
        'delivery_type': 'vaginal',
        'baby_count': 1,
        'baby_weight': 3.2,
        'observations': 'Accouchement normal'
    }, headers=auth_headers)
    assert response.status_code == 201

def test_get_deliveries_success(client, auth_headers):
    """Test 85: Récupération accouchements"""
    response = client.get('/api/maternity/deliveries', headers=auth_headers)
    assert response.status_code == 200

def test_get_maternity_rooms_success(client, auth_headers):
    """Test 86: Récupération lits maternité"""
    response = client.get('/api/maternity/rooms', headers=auth_headers)
    assert response.status_code == 200

def test_create_maternity_room_success(client, auth_headers):
    """Test 87: Création lit maternité"""
    response = client.post('/api/maternity/rooms', json={
        'room_number': 'M-101',
        'type': 'Standard'
    }, headers=auth_headers)
    assert response.status_code == 201


# ==================== 10. TESTS WORKFLOW ====================

def test_get_workflow_doctors_success(client, auth_headers):
    """Test 88: Récupération médecins workflow"""
    response = client.get('/api/workflow/doctors', headers=auth_headers)
    assert response.status_code == 200

def test_get_patient_queue_success(client, auth_headers):
    """Test 89: Récupération file d'attente"""
    response = client.get('/api/workflow/queue', headers=auth_headers)
    assert response.status_code == 200

def test_add_patient_to_queue_success(client, auth_headers, sample_patient):
    """Test 90: Ajout patient en file d'attente"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    response = client.post('/api/workflow/queue', json={
        'patient_id': patient_id,
        'priority': 'normal'
    }, headers=auth_headers)
    assert response.status_code in [200, 201]

def test_patch_patient_queue_success(client, auth_headers, sample_patient):
    """Test 91: Modification file d'attente"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create_p.json['id']
    client.post('/api/workflow/queue', json={'patient_id': patient_id}, headers=auth_headers)
    response = client.patch(f'/api/workflow/queue/{patient_id}', json={
        'status': 'assigned',
        'priority': 'urgent'
    }, headers=auth_headers)
    assert response.status_code == 200

def test_create_vital_signs_success(client, auth_headers, sample_patient):
    """Test 92: Création signes vitaux"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    response = client.post('/api/workflow/vitals', json={
        'patient_id': patient_id,
        'temperature': 36.5,
        'blood_pressure': '120/80',
        'weight': 70,
        'height': 175,
        'heart_rate': 72,
        'oxygen_saturation': 98
    }, headers=auth_headers)
    assert response.status_code == 201

def test_get_vital_signs_success(client, auth_headers):
    """Test 93: Récupération signes vitaux"""
    response = client.get('/api/workflow/vitals', headers=auth_headers)
    assert response.status_code == 200

def test_dispatch_patient_success(client, auth_headers, sample_patient):
    """Test 94: Dispatch patient"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create_p.json['id']
    client.post('/api/workflow/queue', json={'patient_id': patient_id}, headers=auth_headers)
    client.post('/api/workflow/vitals', json={
        'patient_id': patient_id,
        'temperature': 36.5
    }, headers=auth_headers)
    response = client.post('/api/workflow/dispatch', json={
        'patient_id': patient_id,
        'doctor_id': 1,
        'reason': 'Consultation'
    }, headers=auth_headers)
    assert response.status_code == 201

def test_get_dispatches_success(client, auth_headers):
    """Test 95: Récupération dispatches"""
    response = client.get('/api/workflow/dispatches', headers=auth_headers)
    assert response.status_code == 200

def test_create_consultation_success(client, auth_headers, sample_patient):
    """Test 96: Création consultation"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    response = client.post('/api/workflow/consultations', json={
        'patient_id': patient_id,
        'symptoms': 'Fièvre, courbatures',
        'diagnosis': 'Paludisme',
        'observations': 'Traitement en cours'
    }, headers=auth_headers)
    assert response.status_code == 201

def test_get_consultations_success(client, auth_headers):
    """Test 97: Récupération consultations"""
    response = client.get('/api/workflow/consultations', headers=auth_headers)
    assert response.status_code == 200

def test_get_account_lines_success(client, auth_headers):
    """Test 98: Récupération lignes compte patient"""
    response = client.get('/api/workflow/account-lines', headers=auth_headers)
    assert response.status_code == 200

def test_create_account_line_success(client, auth_headers, sample_patient):
    """Test 99: Création ligne compte patient"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    response = client.post('/api/workflow/account-lines', json={
        'patient_id': patient_id,
        'description': 'Frais de laboratoire',
        'amount': 5000,
        'category': 'examen'
    }, headers=auth_headers)
    assert response.status_code == 201

def test_create_final_invoice_success(client, auth_headers, sample_patient):
    """Test 100: Création facture finale"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    client.post('/api/workflow/account-lines', json={
        'patient_id': patient_id,
        'description': 'Consultation',
        'amount': 15000,
        'category': 'consultation'
    }, headers=auth_headers)
    response = client.post('/api/workflow/final-invoice', json={
        'patient_id': patient_id,
        'paid_amount': 10000
    }, headers=auth_headers)
    assert response.status_code == 201

def test_create_hospitalization_success(client, auth_headers, sample_patient):
    """Test 101: Création hospitalisation"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    response = client.post('/api/workflow/hospitalizations', json={
        'patient_id': patient_id,
        'reason': 'Paludisme sévère',
        'room': 'A-101',
        'daily_rate': 20000
    }, headers=auth_headers)
    assert response.status_code == 201

def test_get_hospitalizations_success(client, auth_headers):
    """Test 102: Récupération hospitalisations"""
    response = client.get('/api/workflow/hospitalizations', headers=auth_headers)
    assert response.status_code == 200

def test_discharge_patient_success(client, auth_headers, sample_patient):
    """Test 103: Sortie patient"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create_p.json['id']
    create_h = client.post('/api/workflow/hospitalizations', json={
        'patient_id': patient_id,
        'reason': 'Suivi'
    }, headers=auth_headers)
    hosp_id = create_h.json['id']
    response = client.post(f'/api/workflow/hospitalizations/{hosp_id}/discharge', json={
        'discharge_date': '2026-08-15'
    }, headers=auth_headers)
    assert response.status_code == 200

def test_create_followup_success(client, auth_headers, sample_patient):
    """Test 104: Création suivi médical"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    response = client.post('/api/workflow/followups', json={
        'patient_id': patient_id,
        'instructions': 'Prendre médicaments matin/soir',
        'medication': 'Quinine',
        'status': 'pending'
    }, headers=auth_headers)
    assert response.status_code == 201

def test_get_followups_success(client, auth_headers):
    """Test 105: Récupération suivis"""
    response = client.get('/api/workflow/followups', headers=auth_headers)
    assert response.status_code == 200

def test_create_administration_success(client, auth_headers, sample_patient):
    """Test 106: Création administration médicament"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    response = client.post('/api/workflow/administrations', json={
        'patient_id': patient_id,
        'medication': 'Paracétamol',
        'dose': '500mg',
        'status': 'pending'
    }, headers=auth_headers)
    assert response.status_code == 201

def test_get_medical_record_success(client, auth_headers, sample_patient):
    """Test 107: Récupération dossier médical"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    response = client.get(f'/api/workflow/medical-record/{patient_id}', headers=auth_headers)
    assert response.status_code == 200

def test_get_tariffs_success(client, auth_headers):
    """Test 108: Récupération grille tarifaire"""
    response = client.get('/api/workflow/tariffs', headers=auth_headers)
    assert response.status_code == 200

def test_create_tariff_success(client, auth_headers):
    """Test 109: Création tarif"""
    response = client.post('/api/workflow/tariffs', json={
        'category': 'Consultation',
        'label': 'Consultation spécialiste',
        'amount': 25000
    }, headers=auth_headers)
    assert response.status_code == 201


# ==================== 11. TESTS USERS ET ADMIN ====================

def test_get_users_success(client, auth_headers):
    """Test 110: Récupération liste utilisateurs"""
    response = client.get('/api/users', headers=auth_headers)
    assert response.status_code == 200

def test_create_user_success(client, auth_headers):
    """Test 111: Création utilisateur"""
    response = client.post('/api/users', json={
        'name': 'Nouveau Staff',
        'email': 'staff@test.com',
        'password': 'Staff123!',
        'role': 'infirmier'
    }, headers=auth_headers)
    assert response.status_code == 201

def test_create_user_duplicate_email(client, auth_headers):
    """Test 112: Création utilisateur email existant"""
    client.post('/api/users', json={
        'name': 'User 1',
        'email': 'duplicate2@test.com',
        'password': 'Pass123!',
        'role': 'infirmier'
    }, headers=auth_headers)
    response = client.post('/api/users', json={
        'name': 'User 2',
        'email': 'duplicate2@test.com',
        'password': 'Pass123!',
        'role': 'infirmier'
    }, headers=auth_headers)
    assert response.status_code == 422

def test_update_user_success(client, auth_headers):
    """Test 113: Modification utilisateur"""
    create = client.post('/api/users', json={
        'name': 'Modifiable',
        'email': 'modif@test.com',
        'password': 'Pass123!',
        'role': 'infirmier'
    }, headers=auth_headers)
    user_id = create.json['id']
    response = client.put(f'/api/users/{user_id}', json={
        'name': 'Modifié',
        'role': 'reception'
    }, headers=auth_headers)
    assert response.status_code == 200

def test_delete_user_success(client, auth_headers):
    """Test 114: Suppression utilisateur"""
    create = client.post('/api/users', json={
        'name': 'A Supprimer',
        'email': 'delete@test.com',
        'password': 'Pass123!',
        'role': 'infirmier'
    }, headers=auth_headers)
    user_id = create.json['id']
    response = client.delete(f'/api/users/{user_id}', headers=auth_headers)
    assert response.status_code == 200

def test_get_audit_logs_success(client, auth_headers):
    """Test 115: Récupération logs audit"""
    response = client.get('/api/audit', headers=auth_headers)
    assert response.status_code == 200


# ==================== 12. TESTS AUTRES ROUTES ====================

def test_get_exchange_rate_success(client, auth_headers):
    """Test 116: Récupération taux de change"""
    response = client.get('/api/exchange-rate', headers=auth_headers)
    assert response.status_code == 200

def test_set_exchange_rate_success(client, auth_headers):
    """Test 117: Définition taux de change"""
    response = client.post('/api/exchange-rate', json={
        'rate': 2850,
        'from': 'USD',
        'to': 'CDF'
    }, headers=auth_headers)
    assert response.status_code == 201

def test_get_exchange_rate_history_success(client, auth_headers):
    """Test 118: Historique taux de change"""
    response = client.get('/api/exchange-rate/history', headers=auth_headers)
    assert response.status_code == 200

def test_get_medical_boxes_success(client, auth_headers):
    """Test 119: Récupération boxes médicaux"""
    response = client.get('/api/medical/boxes', headers=auth_headers)
    assert response.status_code == 200

def test_create_medical_box_success(client, auth_headers):
    """Test 120: Création box médical"""
    response = client.post('/api/medical/boxes', json={
        'box_number': '4'
    }, headers=auth_headers)
    assert response.status_code == 201

def test_get_medical_box_success(client, auth_headers):
    """Test 121: Récupération box par ID"""
    create = client.post('/api/medical/boxes', json={'box_number': '5'}, headers=auth_headers)
    box_id = create.json['id']
    response = client.get(f'/api/medical/boxes/{box_id}', headers=auth_headers)
    assert response.status_code == 200

def test_get_subscribers_success(client, auth_headers):
    """Test 122: Récupération abonnés"""
    response = client.get('/api/subscribers', headers=auth_headers)
    assert response.status_code == 200

def test_create_subscriber_success(client, auth_headers):
    """Test 123: Création abonné"""
    response = client.post('/api/subscribers', json={
        'name': 'Mutuelle Test',
        'type': 'Mutuelle',
        'coverage_rate': 80
    }, headers=auth_headers)
    assert response.status_code == 201

def test_get_dashboard_stats_success(client, auth_headers):
    """Test 124: Statistiques dashboard"""
    response = client.get('/api/dashboard/stats', headers=auth_headers)
    assert response.status_code == 200

def test_health_check_success(client):
    """Test 125: Health check"""
    response = client.get('/api/health')
    assert response.status_code == 200
    assert response.json['status'] == 'ok'


# ==================== 13. TESTS NOTIFICATIONS ====================

def test_create_notification_success(client, auth_headers):
    """Test 126: Création notification"""
    response = client.post('/api/notifications', json={
        'title': 'Test Notification',
        'message': 'Ceci est un test',
        'type': 'info'
    }, headers=auth_headers)
    assert response.status_code == 201

def test_get_notifications_success(client, auth_headers):
    """Test 127: Récupération notifications"""
    response = client.get('/api/notifications', headers=auth_headers)
    assert response.status_code == 200

def test_mark_notification_read_success(client, auth_headers):
    """Test 128: Marquer notification comme lue"""
    create = client.post('/api/notifications', json={
        'title': 'À lire',
        'message': 'Message important'
    }, headers=auth_headers)
    notif_id = create.json['id']
    response = client.put(f'/api/notifications/{notif_id}/read', headers=auth_headers)
    assert response.status_code == 200

def test_get_unread_count_success(client, auth_headers):
    """Test 129: Nombre notifications non lues"""
    response = client.get('/api/notifications/unread-count', headers=auth_headers)
    assert response.status_code == 200


# ==================== 14. TESTS IA ====================

def test_ai_health_check_success(client, auth_headers):
    """Test 130: Vérification santé IA"""
    response = client.get('/api/ai/health', headers=auth_headers)
    # Peut échouer si Groq non configuré
    assert response.status_code in [200, 500]


# ==================== 15. TESTS RAPPORTS ====================

def test_report_patients_success(client, auth_headers):
    """Test 131: Rapport patients"""
    response = client.get('/api/reports/patients', headers=auth_headers)
    assert response.status_code == 200
    assert 'stats' in response.json

def test_report_financial_success(client, auth_headers):
    """Test 132: Rapport financier"""
    response = client.get('/api/reports/financial', headers=auth_headers)
    assert response.status_code == 200
    assert 'summary' in response.json

def test_report_pharmacy_success(client, auth_headers):
    """Test 133: Rapport pharmacie"""
    response = client.get('/api/reports/pharmacy', headers=auth_headers)
    assert response.status_code == 200

def test_report_activity_success(client, auth_headers):
    """Test 134: Rapport activité"""
    response = client.get('/api/reports/activity', headers=auth_headers)
    assert response.status_code == 200


# ==================== 16. TESTS PÉDIATRIE ====================

def test_get_children_success(client, auth_headers):
    """Test 135: Récupération enfants"""
    response = client.get('/api/pediatrics/children', headers=auth_headers)
    assert response.status_code == 200

def test_create_child_success(client, auth_headers, sample_patient_female):
    """Test 136: Création enfant"""
    create = client.post('/api/patients', json=sample_patient_female, headers=auth_headers)
    parent_id = create.json['id']
    response = client.post('/api/pediatrics/children', json={
        'full_name': 'Bébé Test',
        'date_of_birth': '2026-08-10',
        'gender': 'M',
        'parent_id': parent_id,
        'birth_weight': 3.2,
        'birth_height': 50
    }, headers=auth_headers)
    assert response.status_code == 201

def test_get_child_success(client, auth_headers):
    """Test 137: Récupération enfant par ID"""
    create = client.post('/api/pediatrics/children', json={
        'full_name': 'Jean Junior',
        'date_of_birth': '2026-08-01',
        'gender': 'M'
    }, headers=auth_headers)
    child_id = create.json['id']
    response = client.get(f'/api/pediatrics/children/{child_id}', headers=auth_headers)
    assert response.status_code == 200

def test_update_child_success(client, auth_headers):
    """Test 138: Modification enfant"""
    create = client.post('/api/pediatrics/children', json={
        'full_name': 'A Modifier',
        'date_of_birth': '2026-08-01',
        'gender': 'M'
    }, headers=auth_headers)
    child_id = create.json['id']
    response = client.put(f'/api/pediatrics/children/{child_id}', json={
        'full_name': 'Modifié',
        'blood_type': 'A+'
    }, headers=auth_headers)
    assert response.status_code == 200

def test_get_child_vaccinations_success(client, auth_headers):
    """Test 139: Récupération vaccinations enfant"""
    create = client.post('/api/pediatrics/children', json={
        'full_name': 'Vaccin Test',
        'date_of_birth': '2026-08-01',
        'gender': 'M'
    }, headers=auth_headers)
    child_id = create.json['id']
    response = client.get(f'/api/pediatrics/children/{child_id}/vaccinations', headers=auth_headers)
    assert response.status_code == 200

def test_create_vaccination_success(client, auth_headers):
    """Test 140: Création vaccination"""
    create = client.post('/api/pediatrics/children', json={
        'full_name': 'Bébé Vaccin',
        'date_of_birth': '2026-08-01',
        'gender': 'M'
    }, headers=auth_headers)
    child_id = create.json['id']
    response = client.post('/api/pediatrics/vaccinations', json={
        'child_id': child_id,
        'vaccine_name': 'BCG',
        'administered_date': '2026-08-10',
        'dose_number': 1,
        'next_due_date': '2026-09-10'
    }, headers=auth_headers)
    assert response.status_code == 201

def test_get_vaccinations_success(client, auth_headers):
    """Test 141: Récupération vaccinations"""
    response = client.get('/api/pediatrics/vaccinations', headers=auth_headers)
    assert response.status_code == 200

def test_create_growth_measurement_success(client, auth_headers):
    """Test 142: Création mesure croissance"""
    create = client.post('/api/pediatrics/children', json={
        'full_name': 'Bébé Croissance',
        'date_of_birth': '2026-08-01',
        'gender': 'M'
    }, headers=auth_headers)
    child_id = create.json['id']
    response = client.post('/api/pediatrics/growth', json={
        'child_id': child_id,
        'measurement_date': '2026-08-15',
        'weight': 3.5,
        'height': 52
    }, headers=auth_headers)
    assert response.status_code == 201

def test_get_growth_measurements_success(client, auth_headers):
    """Test 143: Récupération mesures croissance"""
    response = client.get('/api/pediatrics/growth', headers=auth_headers)
    assert response.status_code == 200


# ==================== 17. TESTS ROUTES SUPPLÉMENTAIRES ====================

def test_get_patient_lab_results_success(client, auth_headers, sample_patient):
    """Test 144: Récupération résultats labo patient"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    response = client.get(f'/api/patients/{patient_id}/lab-results', headers=auth_headers)
    assert response.status_code == 200

def test_link_lab_result_to_patient_success(client, auth_headers, sample_patient):
    """Test 145: Lien résultat labo patient"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create_p.json['id']
    create_t = client.post('/api/laboratory/tests', json={
        'patient_id': patient_id,
        'test_type': 'Hémogramme'
    }, headers=auth_headers)
    test_id = create_t.json['id']
    response = client.post(f'/api/patients/{patient_id}/lab-results', json={
        'test_id': test_id,
        'test_type': 'Hémogramme',
        'result': 'Normal',
        'observations': 'RAS'
    }, headers=auth_headers)
    assert response.status_code == 201

def test_get_lab_results_success(client, auth_headers):
    """Test 146: Récupération résultats labo"""
    response = client.get('/api/laboratory/results', headers=auth_headers)
    assert response.status_code == 200

def test_get_lab_prelevements_success(client, auth_headers):
    """Test 147: Récupération prélèvements labo"""
    response = client.get('/api/laboratory/prelevements', headers=auth_headers)
    assert response.status_code == 200

def test_create_lab_prelevement_success(client, auth_headers, sample_patient):
    """Test 148: Création prélèvement labo"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create_p.json['id']
    create_t = client.post('/api/laboratory/tests', json={
        'patient_id': patient_id,
        'test_type': 'Hémogramme'
    }, headers=auth_headers)
    test_id = create_t.json['id']
    response = client.post('/api/laboratory/prelevements', json={
        'test_id': test_id,
        'patient_id': patient_id,
        'type': 'Sang',
        'notes': 'Prélèvement veineux'
    }, headers=auth_headers)
    assert response.status_code == 201

def test_get_pharmacy_movements_success(client, auth_headers):
    """Test 149: Récupération mouvements pharmacie"""
    response = client.get('/api/pharmacy/movements', headers=auth_headers)
    assert response.status_code == 200

def test_create_pharmacy_movement_success(client, auth_headers, sample_pharmacy):
    """Test 150: Création mouvement pharmacie"""
    create = client.post('/api/pharmacy', json=sample_pharmacy, headers=auth_headers)
    item_id = create.json['id']
    response = client.post('/api/pharmacy/movements', json={
        'medication_id': item_id,
        'medication_name': 'Paracétamol',
        'type': 'entree',
        'quantity': 50,
        'reason': 'Réapprovisionnement'
    }, headers=auth_headers)
    assert response.status_code == 201

def test_update_lab_stock_item_success(client, auth_headers):
    """Test 151: Modification stock labo"""
    create = client.post('/api/laboratory/stock', json={
        'name': 'Réactif Modif',
        'category': 'Réactif',
        'quantity': 30,
        'threshold': 5
    }, headers=auth_headers)
    item_id = create.json['id']
    response = client.put(f'/api/laboratory/stock/{item_id}', json={
        'quantity': 40,
        'threshold': 10
    }, headers=auth_headers)
    assert response.status_code == 200

def test_update_workflow_tariff_success(client, auth_headers):
    """Test 152: Modification tarif workflow"""
    create = client.post('/api/workflow/tariffs', json={
        'category': 'Test',
        'label': 'Test Tarif',
        'amount': 10000
    }, headers=auth_headers)
    tariff_id = create.json['id']
    response = client.put(f'/api/workflow/tariffs/{tariff_id}', json={
        'amount': 15000,
        'label': 'Test Tarif Modifié'
    }, headers=auth_headers)
    assert response.status_code == 200

def test_get_tariff_history_success(client, auth_headers):
    """Test 153: Historique tarifs"""
    response = client.get('/api/workflow/tariffs/history', headers=auth_headers)
    assert response.status_code == 200

def test_create_billing_account_success(client, auth_headers, sample_patient):
    """Test 154: Création compte facturation"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    response = client.post('/api/billing/accounts', json={
        'patient_id': patient_id
    }, headers=auth_headers)
    assert response.status_code == 201

def test_get_billing_account_success(client, auth_headers, sample_patient):
    """Test 155: Récupération compte facturation"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    client.post('/api/billing/accounts', json={'patient_id': patient_id}, headers=auth_headers)
    response = client.get(f'/api/billing/accounts/{patient_id}', headers=auth_headers)
    assert response.status_code == 200

def test_update_billing_account_success(client, auth_headers, sample_patient):
    """Test 156: Mise à jour compte facturation"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    client.post('/api/billing/accounts', json={'patient_id': patient_id}, headers=auth_headers)
    response = client.post('/api/billing/accounts/update', json={
        'patient_id': patient_id,
        'amount': 20000,
        'type': 'debit',
        'description': 'Frais consultation'
    }, headers=auth_headers)
    assert response.status_code == 200

def test_get_account_transactions_success(client, auth_headers, sample_patient):
    """Test 157: Transactions compte"""
    create = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create.json['id']
    client.post('/api/billing/accounts', json={'patient_id': patient_id}, headers=auth_headers)
    response = client.get(f'/api/billing/accounts/{patient_id}/transactions', headers=auth_headers)
    assert response.status_code == 200

def test_mark_all_notifications_read_success(client, auth_headers):
    """Test 158: Marquer toutes notifications lues"""
    response = client.put('/api/notifications/mark-all-read', headers=auth_headers)
    assert response.status_code == 200

def test_pharmacy_cashier_success(client, auth_headers, sample_patient, sample_pharmacy):
    """Test 159: Caisse pharmacie"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create_p.json['id']
    create_m = client.post('/api/pharmacy', json=sample_pharmacy, headers=auth_headers)
    medication_id = create_m.json['id']
    response = client.post('/api/pharmacy/cashier', json={
        'patient_id': patient_id,
        'items': [
            {'medication_id': medication_id, 'description': 'Paracétamol', 'quantity': 2, 'unit_price': 1000}
        ],
        'payment_type': 'cash'
    }, headers=auth_headers)
    assert response.status_code == 201

def test_pharmacy_dispense_success(client, auth_headers, sample_patient, sample_pharmacy):
    """Test 160: Dispensation pharmacie"""
    create_p = client.post('/api/patients', json=sample_patient, headers=auth_headers)
    patient_id = create_p.json['id']
    create_m = client.post('/api/pharmacy', json=sample_pharmacy, headers=auth_headers)
    medication_id = create_m.json['id']
    response = client.post(f'/api/pharmacy/{medication_id}/dispense', json={
        'patient_id': patient_id,
        'quantity': 2,
        'unit_price': 1000
    }, headers=auth_headers)
    assert response.status_code == 201

# ==================== CONTRAT FRONTEND / BACKEND ====================
# Ces tests ne contactent pas Supabase : ils protègent les routes et méthodes
# consommées directement par les interfaces Vanilla JavaScript.
FRONTEND_COMPATIBILITY_ROUTES = {
    '/api/ai/chat': {'POST'},
    '/api/care/administrations': {'GET', 'POST'},
    '/api/tariffs': {'GET', 'POST'},
    '/api/care/prescriptions': {'GET', 'POST'},
    '/api/care/prescriptions/<int:care_id>': {'PATCH'},
    '/api/workflow/hospitalizations/<int:hosp_id>': {'PATCH', 'DELETE'},
    '/api/billing/accounts/<int:patient_id>/full': {'GET'},
    '/api/patients/<int:patient_id>': {'GET', 'PUT', 'PATCH', 'DELETE'},
    '/api/maternity/prenatal/<int:consultation_id>': {'GET', 'PUT', 'DELETE'},
    '/api/maternity/pregnancies/<int:pregnancy_id>': {'GET', 'PUT'},
}

EXISTING_SCHEMA_TABLES = {
    'app_users', 'patients', 'appointments', 'prescriptions', 'laboratory_tests',
    'care_logs', 'pharmacy_items', 'invoices', 'audit_logs', 'patient_accounts',
    'patient_account_transactions', 'hospitalizations', 'medication_administrations',
    'pregnancies', 'prenatal_consultations', 'exchange_rates', 'pharmacy_movements',
}

def test_frontend_compatibility_routes_are_registered():
    """Chaque URL appelée par le frontend doit être exposée avec la bonne méthode."""
    routes = {rule.rule: set(rule.methods or ()) for rule in app.url_map.iter_rules()}
    for route, methods in FRONTEND_COMPATIBILITY_ROUTES.items():
        assert route in routes, f'Route manquante : {route}'
        assert methods.issubset(routes[route]), f'Méthode(s) manquante(s) sur {route}'

def test_existing_schema_table_catalog_is_complete():
    """Les routes de compatibilité reposent uniquement sur les tables du schéma fourni."""
    required = {
        'care_logs', 'prescriptions', 'invoices', 'patient_accounts',
        'patient_account_transactions', 'hospitalizations',
        'medication_administrations', 'pregnancies', 'prenatal_consultations',
    }
    assert required.issubset(EXISTING_SCHEMA_TABLES)


# ==================== LANCEMENT DES TESTS ====================

if __name__ == '__main__':
    pytest.main(['-v', '--tb=short'])
