import os
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import joblib

# Serve static frontend files from the same directory as server.py
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
CORS(app)


@app.route('/')
def index():
    return send_from_directory(STATIC_DIR, 'index.html')


@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)

# Load the trained model, vectorizer, and accuracy
model_accuracy = None
try:
    model = joblib.load(os.path.join(STATIC_DIR, 'model.pkl'))
    vectorizer = joblib.load(os.path.join(STATIC_DIR, 'vectorizer.pkl'))
    model_accuracy = joblib.load(os.path.join(STATIC_DIR, 'accuracy.pkl'))
    print(f"Model loaded successfully. Test accuracy: {model_accuracy:.4f}")
except Exception as e:
    print(f"Error loading model: {e}")
    print("Make sure to run train.py first to generate the model files.")

@app.route('/predict', methods=['POST'])
def predict():
    try:
        data = request.json
        if not data or 'message' not in data:
            return jsonify({'error': 'No message provided'}), 400

        message = data['message']

        # Input validation: reject overly long messages
        if len(message) > 5000:
            return jsonify({'error': 'Message too long (max 5000 characters)'}), 400

        if not hasattr(model, 'predict'):
            return jsonify({'error': 'Model not loaded'}), 500

        # Transform the message using the vectorizer
        transformed_message = vectorizer.transform([message])

        # Predict class and confidence
        prediction = model.predict(transformed_message)[0]
        proba = model.predict_proba(transformed_message)[0]
        confidence = float(max(proba))

        return jsonify({
            'prediction': prediction,
            'confidence': round(confidence, 3),
            'model_accuracy': round(model_accuracy, 4) if model_accuracy else None
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("Starting server on http://127.0.0.1:5000")
    app.run(debug=True, port=5000)