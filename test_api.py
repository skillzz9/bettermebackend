import requests

res = requests.post("http://localhost:8000/nutrition/log_photo", json={
    "user_id": "test",
    "image_base64": "data:image/jpeg;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
})
print(res.status_code)
print(res.text)
