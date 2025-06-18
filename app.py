from flask import Flask, render_template, request, redirect
import sqlite3
import random
import string
import json
from flask_socketio import SocketIO, emit, join_room
from threading import Timer
import os

app = Flask(__name__)
socketio = SocketIO(app)

current_question_data = {}
participants_in_room = {}
DATABASE = 'database.db'

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE
        );
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_code TEXT,
            question TEXT,
            option1 TEXT,
            option2 TEXT
        );
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_code TEXT,
            name TEXT
        );
    ''')

    conn.commit()
    conn.close()
    print("✅ DB 초기화 완료")

def generate_room_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/create_questions')
def create_questions():
    return render_template('create_questions.html')

@app.route('/create_room', methods=['POST'])
def create_room():
    code = generate_room_code()
    conn = get_db()
    conn.execute("INSERT INTO rooms (code) VALUES (?)", (code,))
    conn.commit()
    conn.close()
    return redirect(f'/host/{code}')

@app.route('/host/<code>')
def host_room(code):
    return render_template('host_room.html', code=code)

@app.route('/host/<code>/upload', methods=['GET', 'POST'])
def upload_questions(code):
    if request.method == 'POST':
        file = request.files.get('file')
        if file and file.filename.endswith('.json'):
            data = json.load(file)
            conn = get_db()
            for item in data:
                conn.execute("""
                    INSERT INTO questions (room_code, question, option1, option2)
                    VALUES (?, ?, ?, ?)
                """, (code, item['question'], item['option1'], item['option2']))
            conn.commit()
            conn.close()
            return redirect(f'/host/{code}')  # ✅ 여기 수정
        else:
            return "<h3>❌ 올바른 JSON 파일을 업로드해주세요.</h3>"

    return render_template('upload_questions.html', code=code)


@app.route('/start_game/<code>', methods=['POST'])
def start_game(code):
    conn = get_db()
    first_question = conn.execute(
        "SELECT id FROM questions WHERE room_code = ? ORDER BY id ASC LIMIT 1", 
        (code,)
    ).fetchone()
    conn.close()

    if first_question:
        return redirect(f"/host/{code}")
    else:
        return f"<h3>❌ 질문이 없습니다. 먼저 업로드 해주세요!</h3><a href='/host/{code}'>← 돌아가기</a>"

@app.route('/join', methods=['POST'])
def join():
    code = request.form['code']
    name = request.form['name']

    conn = get_db()
    conn.execute("INSERT INTO participants (room_code, name) VALUES (?, ?)", (code, name))
    conn.commit()
    conn.close()

    return redirect(f"/room/{code}?name={name}")

@app.route('/room/<code>')
def room(code):
    return render_template('join_room.html', code=code)

@app.route('/submit_choice', methods=['POST'])
def submit_choice():
    code = request.form['room_code']
    question_id = int(request.form['question_id'])
    choice = request.form['choice']

    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS choices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_code TEXT,
            question_id INTEGER,
            choice TEXT
        );
    """)
    conn.execute("""
        INSERT INTO choices (room_code, question_id, choice)
        VALUES (?, ?, ?)
    """, (code, question_id, choice))
    conn.commit()

    next_question = conn.execute("""
        SELECT id FROM questions
        WHERE room_code = ? AND id > ?
        ORDER BY id ASC LIMIT 1
    """, (code, question_id)).fetchone()

    conn.close()

    if next_question:
        return redirect(f"/question/{code}/{next_question['id']}")
    else:
        return render_template("game_over.html")

@app.route('/api/questions/<code>')
def api_questions(code):
    conn = get_db()
    result = conn.execute("SELECT * FROM questions WHERE room_code = ? ORDER BY id", (code,)).fetchall()
    conn.close()
    return [dict(row) for row in result]

@socketio.on('join')
def handle_join(data):
    room = data['room']
    name = data.get('name', '익명')
    is_host = data.get('isHost', False)

    join_room(room)

    if not is_host:
        if room not in participants_in_room:
            participants_in_room[room] = []
        participants_in_room[room].append(name)

    emit("update_participants", participants_in_room.get(room, []), to=room)
    print(f"✅ {name}가 방 {room}에 참가했습니다.")


@socketio.on('send_question')
def handle_send_question(data):
    room = data['room']
    question = data['question']
    question_id = data['question_id']

    current_question_data[room] = {
        'question_id': question_id,
        'answers': {'1': 0, '2': 0}
    }

    emit('show_question', question, to=room)
    print(f"🕐 질문 전송: 방 {room} → {question['question']}")

@socketio.on('submit_answer')
def handle_submit_answer(data):
    room = data['room']
    choice = str(data['choice'])

    if room in current_question_data and choice in ['1', '2']:
        current_question_data[room]['answers'][choice] += 1
        print(f"✅ 응답 수집: 방 {room}, 선택 {choice}")

@socketio.on('show_result')
def handle_show_result(data):
    room = data['room']
    qid = data['question_id']

    conn = get_db()
    c1 = conn.execute(
        "SELECT COUNT(*) FROM choices WHERE room_code = ? AND question_id = ? AND choice = '1'",
        (room, qid)
    ).fetchone()[0]
    c2 = conn.execute(
        "SELECT COUNT(*) FROM choices WHERE room_code = ? AND question_id = ? AND choice = '2'",
        (room, qid)
    ).fetchone()[0]
    conn.close()

    total = c1 + c2
    p1 = round((c1 / total) * 100) if total > 0 else 0
    p2 = round((c2 / total) * 100) if total > 0 else 0

    emit('show_result', {
        'question_id': qid,
        'count_1': c1,
        'count_2': c2,
        'percent_1': p1,
        'percent_2': p2
    }, to=room)

@socketio.on('game_over')
def handle_game_over(data):
    room = data['room']
    emit('game_over', to=room)
    print(f"🎮 게임 종료: 방 {room}")

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)
