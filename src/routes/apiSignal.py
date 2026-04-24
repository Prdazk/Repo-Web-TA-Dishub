from flask import Flask, jsonify

app = Flask(__name__)

# Diisi oleh run_flask() saat start via set_shared_counts()
_shared_counts = None

def set_shared_counts(shared_dict):
    global _shared_counts
    _shared_counts = shared_dict

@app.route("/")
def home():
    return jsonify({"message": "Signal detector", "status": "Running"})

@app.route("/counts")
def get_counts():
    if _shared_counts is None:
        return jsonify({})
    return jsonify(dict(_shared_counts))