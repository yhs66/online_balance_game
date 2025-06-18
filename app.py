from flask import Flask, render_template, request, redirect
import sqlite3
import random
import string
import json
from flask_socketio import SocketIO, emit, join_room
from threading import Timer

app = Flask(__name__)
socketio = SocketIO(app)

DATABASE = 'database.db'
current_question_data = {}
room_participants = {}  # 참가자 이름 저장용
room_question_index = {}  # 현재 질문 인덱스 저장

# DB 연결
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# DB 초기화
def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS rooms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE
    );''')
    c.execute('''CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_code TEXT,
        question TEXT,
        option1 TEXT,
        option2 TEXT
    );''')
    c.execute('''CREATE TABLE IF NOT EXISTS choices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_code TEXT,
        question_id INTEGER,
        choice TEXT
    );''')
    conn.commit()
    conn.close()
    print("✅ DB 초기화 완료")

# 랜덤 방 코드 생성
def generate_room_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

# 홈 화면
@app.route('/')
def index():
    return render_template('index.html')

# 질문 생성기
@app.route('/create_questions')
def create_questions():
    return render_template('create_questions.html')

# 방 만들기
@app.route('/create_room', methods=['POST'])
def create_room():
    code = generate_room_code()
    conn = get_db()
    conn.execute("INSERT INTO rooms (code) VALUES (?)", (code,))
    conn.commit()
    conn.close()
    return redirect(f'/host/{code}')

# 호스트 화면
@app.route('/host/<code>')
def host_room(code):
    return render_template('host_room.html', code=code)

# 질문 업로드
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
            return f"<h3>✅ {len(data)}개의 질문이 업로드되었습니다!</h3><a href='/host/{code}'>← 돌아가기</a>"
        else:
            return "<h3>❌ 올바른 JSON 파일을 업로드해주세요.</h3>"
    return render_template('upload_questions.html', code=code)

# 참가자 화면
@app.route('/room/<code>')
def room(code):
    return render_template('join_room.html', code=code)

@app.route('/join', methods=['POST'])
def join():
    code = request.form['code']
    return redirect(f"/room/{code}")

# 소켓: 참가자 입장
@socketio.on('join')
def handle_join(data):
    room = data['room']
    name = data.get('name', '익명')
    role = data.get('role')
    join_room(room)

    if role == "host":
        print(f"✅ 호스트가 {room} 방에 접속")
    else:
        room_participants.setdefault(room, [])
        if name not in room_participants[room]:
            room_participants[room].append(name)
            print(f"👤 참가자 {name} 방 {room} 접속")

    emit("participant_list", room_participants.get(room, []), to=room)

# 소켓: 게임 시작
@socketio.on('start_game')
def handle_start_game(data):
    room = data['room']
    conn = get_db()
    questions = conn.execute("SELECT * FROM questions WHERE room_code = ? ORDER BY id", (room,)).fetchall()
    conn.close()

    room_question_index[room] = 0

    if questions:
        send_question(room, questions[0])

# 소켓: 질문 전송
def send_question(room, q):
    question = {
        'question_id': q['id'],
        'question': q['question'],
        'option1': q['option1'],
        'option2': q['option2']
    }
    current_question_data[room] = {
        'question_id': q['id'],
        'answers': {'1': 0, '2': 0}
    }
    socketio.emit('show_question', question, to=room)
    print(f"📤 질문 전송됨: {q['question']}")

# 소켓: 질문 종료 → 결과 보기
@socketio.on('end_question')
def handle_end_question(data):
    room = data['room']
    send_result(room)

# 소켓: 다음 질문으로 전환
@socketio.on('next_question')
def handle_next_question(data):
    room = data['room']
    conn = get_db()
    questions = conn.execute("SELECT * FROM questions WHERE room_code = ? ORDER BY id", (room,)).fetchall()
    conn.close()

    room_question_index[room] += 1
    idx = room_question_index[room]

    if idx < len(questions):
        send_question(room, questions[idx])
    else:
        socketio.emit("game_over", {}, to=room)

# 소켓: 답변 제출
@socketio.on('submit_answer')
def handle_submit_answer(data):
    room = data['room']
    choice = str(data['choice'])

    if room in current_question_data and choice in ['1', '2']:
        current_question_data[room]['answers'][choice] += 1
        print(f"✅ 응답 수집: 방 {room}, 선택 {choice}")

# 결과 전송
def send_result(room):
    result = current_question_data.get(room, {})
    answers = result.get('answers', {'1': 0, '2': 0})
    total = answers['1'] + answers['2']
    percent_1 = round((answers['1'] / total) * 100) if total else 0
    percent_2 = round((answers['2'] / total) * 100) if total else 0

    socketio.emit('show_result', {
        'question_id': result.get('question_id'),
        'count_1': answers['1'],
        'count_2': answers['2'],
        'percent_1': percent_1,
        'percent_2': percent_2
    }, to=room)

# API: 질문 리스트
@app.route('/api/questions/<code>')
def api_questions(code):
    conn = get_db()
    result = conn.execute("SELECT * FROM questions WHERE room_code = ? ORDER BY id", (code,)).fetchall()
    conn.close()
    return [dict(row) for row in result]

if __name__ == '__main__':
    init_db()
    import os
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)

