import pytest
import firebase_admin

# This special fixture runs automatically before any test in this file.
# It 'patches' the real Firebase functions with fakes so that no credentials are ever needed.
@pytest.fixture(autouse=True)
def mock_firebase_init(mocker):
    # Prevent the actual initialization
    mocker.patch('firebase_admin.initialize_app')
    # Prevent the firestore client from trying to connect
    mocker.patch('firebase_admin.firestore.client')

# Now we can safely import the app, as the Firebase calls will be mocked.
from app import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_index_page(client):
    """
    Tests if the home page ('/') loads correctly.
    """
    response = client.get('/')
    assert response.status_code == 200
    assert b"LINAC QA Backend Running" in response.data

def test_successful_login_with_mocking(client, mocker):
    """
    Tests the /login endpoint by mocking the database call.
    """
    # ARRANGE: Define fake user data for the mock database to return.
    fake_user_data = {
        "status": "active",
        "hospital": "test_hospital",
        "role": "RSO",
        "centerId": "test_hospital"
    }
    
    # We now mock the 'db' object directly within the 'app' module.
    mock_db_get = mocker.patch('app.db.collection')
    
    # Configure the chain of mock calls to return our fake user
    mock_document = mock_db_get.return_value.document.return_value
    mock_get = mock_document.get.return_value
    mock_get.exists = True
    mock_get.to_dict.return_value = fake_user_data

    # ACT: Call the /login endpoint.
    response = client.post('/login', json={'uid': 'any_valid_uid'})

    # ASSERT: Check if the login logic worked correctly.
    assert response.status_code == 200
    assert response.json['status'] == 'success'
    assert response.json['role'] == 'RSO'
    assert response.json['hospital'] == 'test_hospital'
