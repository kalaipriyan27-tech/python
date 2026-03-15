from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import json
from datetime import datetime

app = Flask(__name__, static_folder='.')
CORS(app)

# Simple file-based database for demo purposes
# In production, you would use the GoogleSheets MCP tool
DATABASE_FILE = 'todos.json'

def load_todos():
    """Load todos from JSON file"""
    if os.path.exists(DATABASE_FILE):
        with open(DATABASE_FILE, 'r') as f:
            return json.load(f)
    return []

def save_todos(todos):
    """Save todos to JSON file"""
    with open(DATABASE_FILE, 'w') as f:
        json.dump(todos, f, indent=2)

@app.route('/')
def index():
    """Serve the main HTML page"""
    return send_from_directory('.', 'index.html')

@app.route('/api/todos', methods=['GET'])
def get_todos():
    """Get all todos"""
    todos = load_todos()
    return jsonify(todos)

@app.route('/api/todos', methods=['POST'])
def create_todo():
    """Create a new todo"""
    data = request.json
    todos = load_todos()

    new_todo = {
        'id': len(todos) + 1,
        'title': data.get('title', ''),
        'description': data.get('description', ''),
        'completed': False,
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat()
    }

    todos.append(new_todo)
    save_todos(todos)

    return jsonify(new_todo), 201

@app.route('/api/todos/<int:todo_id>', methods=['PUT'])
def update_todo(todo_id):
    """Update a todo"""
    data = request.json
    todos = load_todos()

    for todo in todos:
        if todo['id'] == todo_id:
            todo['title'] = data.get('title', todo['title'])
            todo['description'] = data.get('description', todo['description'])
            todo['completed'] = data.get('completed', todo['completed'])
            todo['updated_at'] = datetime.now().isoformat()
            save_todos(todos)
            return jsonify(todo)

    return jsonify({'error': 'Todo not found'}), 404

@app.route('/api/todos/<int:todo_id>', methods=['DELETE'])
def delete_todo(todo_id):
    """Delete a todo"""
    todos = load_todos()
    todos = [todo for todo in todos if todo['id'] != todo_id]
    save_todos(todos)

    return jsonify({'message': 'Todo deleted successfully'})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)