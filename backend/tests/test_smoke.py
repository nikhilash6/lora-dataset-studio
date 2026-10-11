def test_health(client):
    body = client.get('/api/health').get_json()
    assert body['ok'] is True and isinstance(body['boot'], str)
