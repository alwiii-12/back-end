import pytest
from app import app # Imports your Flask application

# This sets up a "test client", which is like a fake web browser
# that our test script can use to visit pages on our app.
@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_index_page(client):
    """
    Tests if the home page ('/') loads correctly.
    """
    # ACT: We use our fake browser (the client) to send a GET request
    # to the home page.
    response = client.get('/')
    
    # ASSERT: We check if the response is what we expect.
    
    # 1. Is the status code 200, which means "OK"?
    assert response.status_code == 200
    
    # 2. Does the page contain the correct welcome message?
    #    The 'b' in front of the string means we are checking for the byte representation.
    assert b"LINAC QA Backend Running" in response.data

def test_successful_login_with_mocking(client, mocker):
    """
    Tests the /login endpoint by mocking the database call.
    
    The 'mocker' argument is a special tool from the pytest-mock library
    that lets us temporarily replace parts of our code for the test.
    """
    # ARRANGE: We define a fake user that we want our "mock" database to return.
    fake_user_data = {
        "status": "active",
        "hospital": "test_hospital",
        "role": "RSO",
        "centerId": "test_hospital"
    }
    
    # This is the magic part. We find the function in our code that talks to Firestore
    # (db.collection(...).document(...).get()) and temporarily replace it with a 
    # version that just returns our fake user data.
    mock_get = mocker.patch('app.db.collection.return_value.document.return_value.get')
    mock_get.return_value.exists = True
    mock_get.return_value.to_dict.return_value = fake_user_data

    # ACT: We call the /login endpoint with a test UID.
    response = client.post('/login', json={'uid': 'any_valid_uid'})

    # ASSERT: We check if the login logic worked correctly based on our fake user.
    assert response.status_code == 200
    assert response.json['status'] == 'success'
    assert response.json['role'] == 'RSO'
    assert response.json['hospital'] == 'test_hospital'
