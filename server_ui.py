from flask import Flask, render_template, request, redirect, url_for, session
from flask_socketio import SocketIO, emit, join_room
import random
import os
from datetime import timedelta
import re

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.permanent_session_lifetime = timedelta(days=5)
socketio = SocketIO(app, cors_allowed_origins="*")

users = {}  # 사용자 정보 저장
rooms = {}  # 방 정보 저장
questions = ["가나", "사과", "바나", "당근", "호박", "기차", "자동", "차량", "학교", "연필"]  # 예시 문제

# 한글 자모 분리 함수
def decompose_hangul(char):
    if re.match('[가-힣]', char):
        # 한글 유니코드 계산식
        char_code = ord(char) - ord('가')
        cho = char_code // (21 * 28)  # 초성
        jung = (char_code % (21 * 28)) // 28  # 중성
        jong = char_code % 28  # 종성 (0이면 종성 없음)
        
        # 초성, 중성, 종성 인덱스 반환
        return cho, jung, jong
    return None, None, None

# 한글 자모 비교 및 힌트 생성 함수
def get_hint_type(guess_char, correct_char):
    if guess_char == correct_char:
        return 'carrot'  # 완전 일치
    
    g_cho, g_jung, g_jong = decompose_hangul(guess_char)
    c_cho, c_jung, c_jong = decompose_hangul(correct_char)
    
    if g_cho is None or c_cho is None:
        return 'apple'  # 한글이 아닌 경우
    
    # 일치하는 자모 개수 계산
    matches = 0
    if g_cho == c_cho: matches += 1
    if g_jung == c_jung: matches += 1
    if g_jong == c_jong: matches += 1
    
    # 첫 자음 포함 2개 이상 일치
    if g_cho == c_cho and matches >= 2:
        return 'mushroom'
    
    # 첫 자음 불일치, 나머지 2개 이상 일치
    if g_cho != c_cho and matches >= 2:
        return 'garlic'
    
    # 자음·모음 중 하나만 일치
    if matches == 1:
        return 'eggplant'
    
    # 현재 글자는 불일치, 반대편 글자에서 1개 이상 일치하는지는
    # 다른 글자와의 비교가 필요하므로 여기서는 처리하지 않음
    return 'apple'  # 기본적으로 불일치로 처리

@app.route('/')
def index():
    if 'username' not in session:
        return redirect(url_for('login'))
    session.permanent = True
    return render_template('index.html', rooms=rooms.keys(), username=session['username'])

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username not in users:
            users[username] = {'password': password, 'score': 0}
            return redirect(url_for('login'))
        return render_template('register.html', error='이미 존재하는 사용자 이름입니다.')
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in users and users[username]['password'] == password:
            session.permanent = True
            session['username'] = username
            return redirect(url_for('index'))
        return render_template('login.html', error='잘못된 사용자 이름 또는 비밀번호입니다.')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/create_room', methods=['POST'])
def create_room():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    room_name = request.form['room_name']
    if room_name not in rooms:
        rooms[room_name] = {
            'players': {},
            'max_attempts': 7,
            'status': 'waiting',
            'leaderboard': {}
        }
    return redirect(url_for('game', room_name=room_name))

@app.route('/game/<room_name>')
def game(room_name):
    if 'username' not in session:
        return redirect(url_for('login'))
    
    if room_name not in rooms:
        return redirect(url_for('index'))
    
    return render_template('game.html', room_name=room_name, username=session['username'])

@socketio.on('join')
def on_join(data):
    room = data['room']
    username = data['username']
    
    join_room(room)
    
    if room not in rooms:
        rooms[room] = {
            'players': {},
            'max_attempts': 7,
            'status': 'waiting',
            'leaderboard': {}
        }
    
    # 사용자가 처음 입장하는 경우, 게임 초기화
    if username not in rooms[room]['players']:
        rooms[room]['players'][username] = {
            'attempts': 0,
            'correct_word': random.choice(questions),
            'guesses': [],
            'feedback_history': [],
            'status': 'playing'
        }
    
    # 플레이어 목록 생성
    player_list = []
    for player_name, player_data in rooms[room]['players'].items():
        player_list.append({
            'username': player_name,
            'attempts': player_data['attempts'],
            'status': player_data.get('status', 'playing')
        })
    
    # 현재 방 상태 전송
    emit('room_state', {
        'players': player_list,
        'max_attempts': rooms[room]['max_attempts'],
        'username': username
    }, room=room)
    
    # 다른 플레이어들에게 새 플레이어 입장 알림
    emit('player_joined', {'username': username}, room=room)

@socketio.on('submit_guess')
def handle_guess(data):
    room = data['room']
    username = data['username']
    guess = data['guess']
    
    if room not in rooms or username not in rooms[room]['players']:
        return
    
    player_data = rooms[room]['players'][username]
    
    # 이미 게임이 끝난 플레이어인지 확인
    if player_data.get('status', 'playing') != 'playing':
        emit('error', {'message': '이미 게임이 종료되었습니다.'}, room=room)
        return
    
    # 최대 시도 횟수 확인
    max_attempts = rooms[room]['max_attempts']
    if player_data['attempts'] >= max_attempts:
        player_data['status'] = 'lose'
        emit('game_over', {
            'username': username, 
            'correct_word': player_data['correct_word'],
            'result': 'lose'
        }, room=room)
        return
    
    # 단어 길이 확인
    if len(guess) != 2:
        emit('error', {'message': '두 글자를 입력하세요.'}, room=room)
        return
    
    # 추측 기록 및 시도 횟수 증가
    player_data['guesses'].append(guess)
    player_data['attempts'] += 1
    
    correct_word = player_data['correct_word']
    feedback = []
    
    # 첫 글자 힌트
    if guess[0] == correct_word[0]:
        feedback.append('carrot')  # 정확히 일치
    else:
        # 쌍근 규칙에 따른 힌트
        char_hint = get_hint_type(guess[0], correct_word[0])
        
        # 반대편 글자와의 비교 (banana 규칙)
        if char_hint == 'apple' and (
            guess[0] == correct_word[1] or 
            (decompose_hangul(guess[0])[0] == decompose_hangul(correct_word[1])[0])
        ):
            feedback.append('banana')  # 반대편 글자와 일치
        else:
            feedback.append(char_hint)
    
    # 두번째 글자 힌트
    if guess[1] == correct_word[1]:
        feedback.append('carrot')  # 정확히 일치
    else:
        # 쌍근 규칙에 따른 힌트
        char_hint = get_hint_type(guess[1], correct_word[1])
        
        # 반대편 글자와의 비교 (banana 규칙)
        if char_hint == 'apple' and (
            guess[1] == correct_word[0] or 
            (decompose_hangul(guess[1])[0] == decompose_hangul(correct_word[0])[0])
        ):
            feedback.append('banana')  # 반대편 글자와 일치
        else:
            feedback.append(char_hint)
    
    # 피드백 기록 저장
    player_data['feedback_history'].append({
        'guess': guess,
        'feedback': feedback
    })
    
    # 결과 전송
    emit('guess_result', {
        'username': username,
        'guess': guess,
        'feedback': feedback,
        'attempts': player_data['attempts'],
        'remaining': max_attempts - player_data['attempts']
    }, room=room)
    
    # 플레이어 목록 업데이트
    player_list = []
    for player_name, p_data in rooms[room]['players'].items():
        player_list.append({
            'username': player_name,
            'attempts': p_data['attempts'],
            'status': p_data.get('status', 'playing')
        })
    
    emit('players_update', {'players': player_list}, room=room)
    
    # 정답을 맞춘 경우
    if guess == correct_word:
        player_data['status'] = 'win'
        score = max_attempts - player_data['attempts'] + 1  # 남은 기회에 따른 점수 계산
        rooms[room]['leaderboard'][username] = rooms[room]['leaderboard'].get(username, 0) + score
        
        emit('game_over', {
            'username': username,
            'correct_word': player_data['correct_word'],
            'result': 'win',
            'score': score,
            'total_score': rooms[room]['leaderboard'][username]
        }, room=room)
        
        # 리더보드 업데이트
        leaderboard = []
        for player, score in rooms[room]['leaderboard'].items():
            leaderboard.append({'username': player, 'score': score})
        leaderboard.sort(key=lambda x: x['score'], reverse=True)
        
        emit('leaderboard_update', {'leaderboard': leaderboard}, room=room)
    
    # 모든 기회를 소진한 경우
    elif player_data['attempts'] >= max_attempts:
        player_data['status'] = 'lose'
        emit('game_over', {
            'username': username,
            'correct_word': player_data['correct_word'],
            'result': 'lose'
        }, room=room)

@socketio.on('restart_game')
def restart_game(data):
    room = data['room']
    username = data['username']
    
    if room in rooms and username in rooms[room]['players']:
        # 새 게임 설정
        rooms[room]['players'][username] = {
            'attempts': 0,
            'correct_word': random.choice(questions),
            'guesses': [],
            'feedback_history': [],
            'status': 'playing'
        }
        
        emit('game_restarted', {'username': username}, room=room)
        
        # 플레이어 목록 업데이트
        player_list = []
        for player_name, player_data in rooms[room]['players'].items():
            player_list.append({
                'username': player_name,
                'attempts': player_data['attempts'],
                'status': player_data.get('status', 'playing')
            })
        
        emit('players_update', {'players': player_list}, room=room)

@socketio.on('get_player_state')
def handle_get_player_state(data):
    room = data['room']
    target_username = data['target_username']
    
    if room in rooms and target_username in rooms[room]['players']:
        player_data = rooms[room]['players'][target_username]
        
        emit('player_state_update', {
            'username': target_username,
            'attempts': player_data['attempts'],
            'guesses': player_data['guesses'],
            'status': player_data.get('status', 'playing'),
            'feedback_history': player_data.get('feedback_history', [])
        })

if __name__ == "__main__":
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)