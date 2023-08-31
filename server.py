
import dataclasses
import math
import os
import time
import uuid
import firebase_admin
import pyrebase
import eventlet
eventlet.monkey_patch(thread=False)
from firebase_admin import credentials, auth
from firebase_admin import firestore, storage
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, join_room, leave_room, emit, close_room
from default_rooms import get_mock_rooms
from models import Room, User

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "etc/secrets/debate-center-firebase-key.json"
app = Flask(__name__)
origins = ["https://debate-center-dd720.web.app", "https://debate-center-dd720.firebaseapp.com"]
CORS(app, origins=origins)
socketio = SocketIO(app, cors_allowed_origins=origins)

config = {
    'apiKey': os.environ.get('FIREBASE_API_KEY'),
    'authDomain': os.environ.get('FIREBASE_AUTH_DOMAIN'),
    'databaseURL': os.environ.get('FIREBASE_DATABASE_URL'),
    'projectId': os.environ.get('FIREBASE_PROJECT_ID'),
    'storageBucket': os.environ.get('FIREBASE_STORAGE_BUCKET'),
    'messagingSenderId': os.environ.get('FIREBASE_MESSAGING_SENDER_ID'),
    'appId': os.environ.get('FIREBASE_APP_ID')
}

# Initialize Firebase Admin SDK for Firestore
cred_firestore = credentials.Certificate("etc/secrets/debate-center-firebase-key.json")
app_firestore = firebase_admin.initialize_app(cred_firestore, name='Firestore', options={
    'storageBucket': config['storageBucket']
})

db_firestore = firestore.client(app_firestore)

# Auth
firebase_auth = pyrebase.initialize_app(config)
auths = firebase_auth.auth()

@app.route('/', methods=['GET'])
def index():
    return "hello this is me maaario"

@socketio.on('connect')
def handle_connect():
    print('Client connected. id=', request.sid)

def is_username_exists(username):
    query = db_firestore.collection('users').where('username', '==', username).limit(1)
    results = query.stream()
    return len(list(results)) > 0

# ---------- GET CONFIG ---------- #
@app.route('/api/get_auth', methods=['GET'])
def get_auth():
    return jsonify(config)

# ---------- SIGN UP ---------- #
@app.route('/api/signup', methods=['POST'])
def signup():
    # sign up user without image
    user_data = request.get_json()
    email = user_data.get('email')
    password = user_data.get('password')
    name = user_data.get('name')
    username = user_data.get('username')
    tags = user_data.get('tags')

    print(f"!!! signup requsted: \nemail: {email}\npassword: {password}\nname: {name}\nusername: {username}\ntags: {tags}")

    try:
        if is_username_exists(username):
            raise Exception('Username already exists')
        
        # create new user base on email and password
        new_user = auths.create_user_with_email_and_password(email=email, password=password)
        login_user = auths.sign_in_with_email_and_password(email, password)
        token = login_user['idToken']
        user_info = auths.get_account_info(token)['users'][0]
        user_id = user_info['localId']

        # Add user details to database
        user_ref = db_firestore.collection('users').document(user_id)
        user_ref.set({
            'name': name,
            'username': username,
            'tags':tags
        })
        
        # Return success response
        return jsonify({'message': 'Signup successful', 'userId': username, 'token': token, 'tags': tags }), 200
    
    except Exception as e:
        # Handle other errors
        if 'Username already exists' in str(e):
            return jsonify({'username': 'Username already exists'}), 500
        else:
            return jsonify({'email': 'Invalid email or already exists'}), 500

# ---------- SIGN UP - Upload Image to Storage ---------- #
@app.route('/api/upload_image', methods=['POST'])
def upload_file():

    username = request.form['username']
    token = request.form['token']
    user_info = auths.get_account_info(token)['users'][0]
    user_id = user_info['localId']
    user_ref = db_firestore.collection('users').document(user_id)

    if 'file' not in request.files:
        user_ref.update({
            'image': ''
        })
        return jsonify({'message': 'Empty upload successful', 'profilePhotoURL': ''})
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    # Upload the file to Firebase Storage
    destination_blob_name = f'users/{username}/profile _image'
    bucket = storage.bucket(app=app_firestore)
    # Check if the blob already exists
    try:
        existing_blob = bucket.get_blob(destination_blob_name)
        if existing_blob:
            blob = existing_blob
            blob.upload_from_file(file, content_type=file.content_type)
            profilePhotoURL = manage_blob(blob, username, False)
        else:
            blob = bucket.blob(destination_blob_name)
            blob.upload_from_file(file, content_type=file.content_type)
            profilePhotoURL = manage_blob(blob, username, True)
    except Exception as e:
        blob = bucket.blob(destination_blob_name)
        blob.upload_from_file(file, content_type=file.content_type)
        profilePhotoURL = manage_blob(blob, username, True)
    
    user_ref.update({
        'image': profilePhotoURL
        })



    return jsonify({'message': 'Upload successful', 'profilePhotoURL': profilePhotoURL})

def manage_blob(blob, username ,is_first_time):
    try:
        destination_blob_name_url = f'users%2F{username}%2Fprofile _image'
        token = uuid.uuid4()
        metageneration_match_precondition = None
        metadata = {"firebaseStorageDownloadTokens": token}
        blob.metadata = metadata
        blob.make_public()
        blob.patch(if_metageneration_match=metageneration_match_precondition)
        # Fetches a public URL from GCS.
        gcs_storageURL = blob.public_url
        # Generates a URL with Access Token from Firebase.
        firebase_storageURL = 'https://firebasestorage.googleapis.com/v0/b/{}/o/{}?alt=media&token={}'.format(blob.bucket.name, destination_blob_name_url, token)
        return firebase_storageURL
    except Exception as e:
        return jsonify({'error': e}), 500



# ---------- SIGN IN REGULAR ---------- #
@app.route('/api/signin', methods=['POST'])
def signin():
    user_data = request.get_json()
    email = user_data.get('email')
    password = user_data.get('password')
    print(f"!!! signin requsted: \nemail: {email}\npassword: {password}")
    try:
        login_user = auths.sign_in_with_email_and_password(email, password)
        token = login_user['idToken']
        user_info = auths.get_account_info(token)['users'][0]
        user_id = user_info['localId']
        users_ref = db_firestore.collection('users').document(user_id)
        users_user_data = users_ref.get().to_dict()
        username = users_user_data['username']
        tags = users_user_data['tags']
        try:
            profilePhotoURL = users_user_data['image']
        except Exception as e:
            profilePhotoURL = ''

        # Return success response
        return jsonify({'message': 'Login successful', 'userId': username, 'token': token, 'tags': tags, 'profilePhotoURL': profilePhotoURL }), 200
    
    except Exception as e:
        # Handle other errors
        return jsonify({'error': str(e)}), 500

# ---------- USER INFO ---------- #
@app.route('/api/user', methods=['GET'])
def get_user():
    id_token = request.headers.get('Authorization')
    try:
        decoded_token = auths.get_account_info(id_token)['users'][0]
        user_uid = decoded_token['localId']
        provider = decoded_token['providerUserInfo'][0]['providerId']
        user_doc = {}
        user_doc["email"] = decoded_token['email']
        user_doc["provider"] = provider
        user_ref = db_firestore.collection('users').document(user_uid)
        user_doc.update(user_ref.get().to_dict())
        return jsonify(user_doc)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/check_user_data', methods=['GET'])
def check_user_data():
    user_id= request.headers.get('UserId')
    try:
        user_ref = db_firestore.collection('users').document(user_id)
        user_doc = user_ref.get().to_dict()
        return jsonify(user_doc)
    except Exception as e:
        return

# ---------- UPDATE_USER ---------- #
@app.route('/api/update_user', methods=['POST'])
def update_user():
    user_data = request.get_json()
    user_dict = dict(user_data)
    try:
        user_dict.pop('token')

    except Exception as e:
        pass

    try:
        token = user_data.get('token')
        user_info = auths.get_account_info(token)['users'][0]
        user_id = user_info['localId']
        user_ref = db_firestore.collection('users').document(user_id).update(user_dict)
        return jsonify({'message': 'Update successful', "tags": user_dict["tags"] }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
# ---------- UPDATE_STAGE_USER ---------- #
@app.route('/api/update_stage_user', methods=['POST'])
def update_stage_user():
    user_data = request.get_json()
    user_dict = dict(user_data)
    try:
        if is_username_exists(user_dict['username']):
            raise Exception('Username already exists')  
        user_dict.pop('token')

    except Exception as e:
        if 'Username already exists' in str(e):
            return jsonify({'error': 'Username already exists'}), 500
    
    # the first time google/facebook account login
    token = user_data.get('token')
    user_info = auths.get_account_info(token)['users'][0]
    user_id = user_info['localId']
    user_ref = db_firestore.collection('users').document(user_id).set(dict(user_data))
    return jsonify({'message': 'Create Database, Update successful', 'token': token, 'userId': user_data["username"], "tags": user_dict["tags"]  }), 200

# ---------- DELETE USER ---------- #
@app.route('/api/delete_user', methods=['POST'])
def delete_user():
    user_data = request.get_json()
    try:
        token = user_data.get('token')
        user_info = auths.get_account_info(token)['users'][0]
        user_id = user_info['localId']
        print("here1")
        auths.delete_user_account(token)
        print("here2")
        user_ref = db_firestore.collection('users').document(user_id).delete()
        print("here3")

        destination_blob_name = f'users/{user_data.get("username")}/profile _image'
        print(destination_blob_name)
        bucket = storage.bucket(app=app_firestore)
        # Check if the blob already exists
        try:
            existing_blob = bucket.get_blob(destination_blob_name)
            if existing_blob:
                print("deleting blob")
                existing_blob.delete()
        except Exception as e:
            pass

        return jsonify({'message': 'Delete successful', 'userId': user_data.get('username'), 'token': token }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# user_to_socket = {}
# room_to_users = {}
# room_to_sockets = {}
socket_to_user = {}
socket_to_room = {}
rooms = get_mock_rooms()

# ---------- HOME PAGE ---------- #
@socketio.on("fetch_all_rooms")
def get_all_rooms():
    sid = request.sid

    print(f"!!! fetch_all_rooms sid: {sid}")
    rooms_to_send = {room_id: dataclasses.asdict(room_data) for room_id, room_data in rooms.items()}
    socketio.emit("all_rooms", rooms_to_send, room=sid)


# ---------- CREATE ROOM PAGE ---------- #
@app.route('/api/create_room', methods=['POST'])
def create_room():
    room_data = request.json
    room_id = uuid.uuid4().hex

    room = Room(
        id = room_id,
        name=room_data.get('name'),
        tags=room_data.get('tags'),
        teams=room_data.get('teams'),
        team_names=room_data.get('teamNames'),
        room_size=room_data.get('room_size'),
        time_to_start=time.time() + room_data.get('time_to_start') * 60,
        allow_spectators=room_data.get('allow_spectators'),
        users_list={},
        spectators_list={},
        moderator=room_data.get('moderator'),
        is_conversation=False,
        pictureId=room_data.get('pictureId', -1),
        blacklist=[],
        user_reports={},
    )
    rooms[room_id] = room
    socketio.emit('rooms_new', dataclasses.asdict(room))

    print(f"!!! create_room room_id: {room_id}, room_data: {room_data}")

    # Return the room ID as a response
    return jsonify({'roomId': room_id})


# -------------- ROOM PAGE ------------- #
@socketio.on('join_room')
def join_debate_room(data):
    # Get the request data
    sid = request.sid
    room_id = data.get('roomId')
    user_id = data.get('userId')
    photo_url = data.get('photoUrl')
    print("photo_url: ", photo_url)
    if user_id is None or room_id is None:
        print("user_id or room_id is None")
        return
    print(f"join_room room_id: {room_id}, sid: {sid}, user_id: {user_id}")

    if room_id not in rooms:
        # Room not found, send a specific response
        socketio.emit('room not found', {'error': 'Room not found'}, room=sid)
        return

    room = rooms[room_id]

    if user_id in room.blacklist:
        socketio.emit('kick from room', room=sid)
        return

    # if no moderator, set the user to be the moderator
    if not room.moderator:
        room.moderator = user_id

    if user_id in room.users_list or user_id in room.spectators_list:
        print("user tried to join room he is already in")
        # update the user's socket id
        old_sid = room.users_list[user_id].sid if user_id in room.users_list else room.spectators_list[user_id].sid
        socket_to_room.pop(old_sid, None)
        socket_to_user.pop(old_sid, None)
        leave_room(room_id, sid=old_sid)
        socket_to_room[sid] = room_id
        socket_to_user[sid] = user_id
        if user_id in room.users_list:
            room.users_list[user_id].sid = sid 
        else:
            room.spectators_list[user_id].sid = sid
        socketio.emit('user_join', dataclasses.asdict(room), room=sid)
        socketio.emit('room_data_updated', dataclasses.asdict(room), to=room_id)
        socketio.emit('rooms_updated', dataclasses.asdict(room), skip_sid=room_id)
        join_room(room_id)
        return
    
    elif any(user_id in another_room.users_list for another_room in rooms.values()):
        print("user tried to join room when he is already in another room")
        socketio.emit('user already in another room', room=sid)
        return
    
    if room.is_conversation:
        if not room.allow_spectators:
            emit('conversation already started', room=sid)
            return
        
        room.spectators_list[user_id] = User(sid=sid, photo_url=photo_url)
        socketio.emit('spectator_join', dataclasses.asdict(room), room=sid)

    elif len(room.users_list) >= room.room_size:
        if not room.allow_spectators:
            # Room is full, send a specific response
            socketio.emit('room is full', room=sid)
            return
        # TODO: add user to spectators list
        room.spectators_list.append(user_id)
        socketio.emit('user_join', dataclasses.asdict(room) ,room=sid)

    else:  # room is not full, add user to room
        team = room.teams and len([other_user for other_user in room.users_list.values() if other_user.team]) < len(room.users_list) / 2
        room.users_list.update({user_id: User(sid=sid, team=team, photo_url=photo_url)})
        if user_id not in room.user_reports.keys():
            room.user_reports[user_id] = []
        socketio.emit('user_join', dataclasses.asdict(room), room=sid)
        
    # Notify all users in the room about the change
    socketio.emit('room_data_updated', dataclasses.asdict(room), to=room_id)
    socketio.emit('rooms_updated', dataclasses.asdict(room), skip_sid=room_id)
    
    # Join the SocketIO broadcast room
    socket_to_room[sid] = room_id
    socket_to_user[sid] = user_id
    join_room(room_id)
    
    # Join the SocketIO broadcast room
    socket_to_room[sid] = room_id
    socket_to_user[sid] = user_id
    join_room(room_id)
    

@socketio.on('leave_click')
def leave_debate_room(data):
    # Get the request data
    sid = request.sid
    room_id = data.get('roomId')
    user_id = data.get('userId')

    if sid in socket_to_room:
        socket_to_room.pop(sid)
    if sid in socket_to_user:
        socket_to_user.pop(sid)

    if room_id not in rooms:
        # Room not found, send a specific response
        socketio.emit('leave_room_error', {'error': 'Room not found'}, room=sid)
        return

    room = rooms[room_id]

    if user_id in room.users_list:
        room.users_list.pop(user_id)
    elif user_id in room.spectators_list:
        room.spectators_list.pop(user_id)
    else:
        emit('leave_room_error', {'error': 'User is not in the room'}, room=sid)
        leave_room(room_id)
        return
    
    # delete user_id from user_reports list
    for other_user in room.users_list.keys():
        if user_id in room.user_reports[other_user]:
            room.user_reports[other_user].remove(user_id)
    
    # delete user_id from user_reports
    room.user_reports.pop(user_id)
    
    # check users_report after user leaves
    for check_user in room.users_list.keys():
        if  len(room.user_reports[check_user]) >= int(len(room.users_list) / 2) + 1 :
            room.blacklist.append(check_user)
            socketio.emit('check_report_user_list',{'reportedUserId':check_user,
                            'roomData': dataclasses.asdict(room)} ,to=room_id)
                            
    if not room.users_list and room.is_conversation:
        # Delete the conversation if no users are left, send a message to the spectators
        leave_room(room_id)
        socketio.emit('allUsersLeft', to=room_id)
        rooms.pop(room_id)
        close_room(room_id)
        socketio.emit('rooms_deleted', dataclasses.asdict(room), skip_sid=room_id)
        bot_room_manager.remove_room(room_id)
        return

    # If the moderator left, assign a new moderator
    if user_id == room.moderator:
        if room.users_list:
            room.moderator = list(room.users_list.keys())[0]
        elif room.spectators_list:
            room.moderator = list(room.spectators_list.keys())[0]
        else:
            room.moderator = None

    # leave the SocketIO broadcast room
    leave_room(room_id)
    # Notify all users in the room about the change
    socketio.emit('room_data_updated', dataclasses.asdict(room), to=room_id)
    socketio.emit('rooms_updated', dataclasses.asdict(room), skip_sid=room_id)
    socketio.emit('userLeft', { "sid": sid, "userId": user_id }, to=room_id)  # for conversations only


@socketio.on('fetch_room_data')
def fetch_room_data(data):
    # Get the request data
    sid = request.sid
    room_id = data.get('roomId')

    if room_id not in rooms:
        # Room not found, send a specific response
        socketio.emit('fetch_room_data_error', {'error': 'Room not found'}, room=sid)
        return

    room = rooms[room_id]
    socketio.emit('room_data', dataclasses.asdict(room), room=sid)

# -------------------------------------- #

# ------------- USERS SHOW ------------- #
@socketio.on('switch_team')
def switch_team(data):
    room_id = data.get('roomId')
    user_id = data.get('userId')

    if room_id not in rooms:
        return

    room = rooms[room_id]
    
    if user_id not in room.users_list:
        return
    
    user = room.users_list[user_id]
    user.team = not user.team

    # Notify all users in the room about the change
    socketio.emit('room_data_updated', dataclasses.asdict(room), to=room_id)

@socketio.on('spectator_click')
def handle_spectator_click(data):
    room_id = data.get('roomId')
    user_id = data.get('userId')
    print(f"spectator_click room_id: {room_id}, sid: {request.sid}, user_id: {user_id}")
    if room_id not in rooms:
        return
    
    room = rooms[room_id]
    
    if user_id not in room.users_list:
        return
    user = room.users_list.pop(user_id)
    room.spectators_list[user_id] = user
    socketio.emit('room_data_updated', dataclasses.asdict(room), to=room_id)

@socketio.on('debater_click')
def handle_debater_click(data):
    room_id = data.get('roomId')
    user_id = data.get('userId')
    print(f"debater_click room_id: {room_id}, sid: {request.sid}, user_id: {user_id}")
    if room_id not in rooms:
        return
    
    room = rooms[room_id]
    
    if user_id not in room.spectators_list:
        return
    user = room.spectators_list.pop(user_id)
    room.users_list[user_id] = user
    # if teams are enabled, check if teams would become unbalanced
    if room.teams and len([other_user for other_user in room.users_list.values() if other_user.team == user.team]) > math.ceil(room.room_size / 2):
        user.team = not user.team
    socketio.emit('room_data_updated', dataclasses.asdict(room), to=room_id)

@socketio.on('ready_click')
def handle_ready_click(data):
    room_id = data.get('roomId')
    user_id = data.get('userId')
    print(f"ready_click room_id: {room_id}, sid: {request.sid}, user_id: {user_id}")

    if room_id not in rooms:
        return
    
    room = rooms[room_id]
    
    if user_id not in room.users_list:
        return
    
    user = room.users_list[user_id]
    user.ready = not user.ready

    # Notify all users in the room about the change
    socketio.emit('room_data_updated', dataclasses.asdict(room), to=room_id)

@socketio.on('report_user')
def report_user(data):
    reported_user_id = data.get('reportedUserId')
    user_id = data.get('userId')
    room_id = data.get('roomId')
    print(f"reported_user_id: {reported_user_id}, user_id: {user_id},roomId: {room_id} ")

    if room_id not in rooms:
        return
    
    room = rooms[room_id] 

    # add or remove from user_reports
    if reported_user_id not in room.user_reports.keys():
        room.user_reports[reported_user_id] = []

    if user_id not in room.user_reports[reported_user_id]:
        room.user_reports[reported_user_id].append(user_id)
    else:
        room.user_reports[reported_user_id].remove(user_id)

    # update blacklist
    if  len(room.user_reports[reported_user_id]) >= int(len(room.users_list) / 2) + 1 :
        room.blacklist.append(reported_user_id)
        socketio.emit('check_report_user_list',{'reportedUserId':reported_user_id,
                        'roomData': dataclasses.asdict(room)} ,to=room_id)


    # Notify all users in the room about the change
    socketio.emit('room_data_updated', dataclasses.asdict(room), to=room_id)


@socketio.on('kick_user')
def kick_user(data):
    # Get the request data
    sid = request.sid
    room_id = data.get('roomId')
    user_id = data.get('userId')

    if sid in socket_to_room:
        socket_to_room.pop(sid)
    if sid in socket_to_user:
        socket_to_user.pop(sid)

    if room_id not in rooms:
        # Room not found, send a specific response
        socketio.emit('leave_room_error', {'error': 'Room not found'}, room=sid)
        return

    room = rooms[room_id]

    # delete user_id from user_reports list
    for other_user in room.users_list.keys():
        if user_id in room.user_reports[other_user]:
            room.user_reports[other_user].remove(user_id)
    
    # delete user_id from users_list and user_reports
    if user_id in room.users_list:
        room.users_list.pop(user_id)
    elif user_id in room.spectators_list:
        room.spectators_list.pop(user_id)
    else: 
        socketio.emit('leave_room_error', {'error': 'User is not in the room'}, room=sid)
        leave_room(room_id)
        return
    
    room.user_reports.pop(user_id)
    
    # check users_report after user leaves
    for check_user in room.users_list.keys():
        if  len(room.user_reports[check_user]) >= int(len(room.users_list.keys()) / 2) + 1 :
            room.blacklist.append(check_user)
            socketio.emit('check_report_user_list',{'reportedUserId':check_user,
                            'roomData': dataclasses.asdict(room)} ,to=room_id)

    if not room.users_list and room.is_conversation:
        # Delete the conversation if no users are left, send a message to the spectators
        leave_room(room_id)
        socketio.emit('allUsersLeft', to=room_id)
        rooms.pop(room_id)
        close_room(room_id)
        socketio.emit('rooms_deleted', dataclasses.asdict(room), skip_sid=room_id)
        return

    # If the moderator left, assign a new moderator
    if user_id == room.moderator:
        if room.users_list:
            room.moderator = list(room.users_list.keys())[0]
        elif room.spectators_list:
            room.moderator = list(room.spectators_list.keys())[0]
        else:
            room.moderator = None

    # leave the SocketIO broadcast room
    leave_room(room_id)
    # Notify all users in the room about the change
    socketio.emit('room_data_updated', dataclasses.asdict(room), to=room_id)
    socketio.emit('rooms_updated', dataclasses.asdict(room), skip_sid=room_id)
    socketio.emit('userLeft', { "sid": sid, "userId": user_id }, to=room_id)  # for conversations only

# -------------------------------------- #

# -------------- CONVERSATION PAGE ------------- #

@socketio.on('start_conversation_click')  # TODO: add a thread that checks the timer for each room and starts conversation when it reaches 0
def handle_conversation_start(data):
    room_id = data.get('roomId')

    if room_id not in rooms:
        return
    
    rooms[room_id].is_conversation = True
    
    # Notify all users in the room about the change
    socketio.emit('conversation_start', to=room_id)

    # Bot room manager
    if rooms[room_id].teams is True:
        bot_room_manager.add_room(room_id)


@socketio.on('WebcamReady')  # TODO: add a thread that checks the timer for each room and starts conversation when it reaches 0
def handle_webcam_ready(payload):
    sid = request.sid
    print("WebcamReady from:", sid, payload)
    room_id = payload.get('roomId')
    user_id = payload.get('userId')
    room = rooms[room_id]
    user = room.users_list[user_id]
    if user.sid != sid:
        print("WebcamReady: user.sid is not equal to request.sid, quiting", user.sid, request.sid)
        return

    user.camera_ready = True
    # Notify all users in the room about the change
    socketio.emit('userInConversationReady', { "userId": user_id, "userSid": user.sid }, to=room_id, include_self=False)
    # Notify user about other user in the room - not needed
    socketio.emit('usersInConversation', dataclasses.asdict(room), room=sid)

# -------------- SIGNALING ------------- #
    
@socketio.on('sendingSignal')
def handle_sending_signal(payload):
    user_sid_to_send_signal = payload['userSidToSendSignal']
    user_id = payload['userId']
    # caller_id should be request.sid
    print(f"got sendingSignal from user: {user_id}, sid: {request.sid}, caller_id: {payload['callerId']}, is_spectator: {payload['isSpectator']}, to: {user_sid_to_send_signal}")
    print(f"sending sendingSignalAck to sid: {user_sid_to_send_signal}")
    socketio.emit('sendingSignalAck', {'signal': payload['signal'], 'callerId': payload['callerId'], 'userId': user_id, 'isSpectator': payload['isSpectator']}, to=user_sid_to_send_signal)

@socketio.on('returningSignal')
def handle_returning_signal(payload):
    caller_id = payload['callerId']
    user_id = payload['userId']
    print(f"got returningSignal from user: {user_id}, sid: {request.sid}, caller_id: {caller_id}")
    print(f"sending returningSignalAck to sid: {caller_id}")
    socketio.emit('returningSignalAck', {'signal': payload['signal'], 'calleeId': request.sid, 'userId': user_id}, to=caller_id)

@socketio.on('disconnect')
def handle_disconnect():
    # Get the request data
    sid = request.sid
    room_id = socket_to_room.get(sid)
    user_id = socket_to_user.get(sid)
    print(f'Client disconnected with sid: {sid}, room_id: {room_id}, user_id: {user_id}',)

    if sid in socket_to_room:
        socket_to_room.pop(sid)
    if sid in socket_to_user:
        socket_to_user.pop(sid)

    if room_id is None or room_id not in rooms:
        return
    room = rooms[room_id]
    if user_id is None:
        return
    if user_id in room.users_list:
        room.users_list.pop(user_id)
    if user_id in room.spectators_list:
        room.spectators_list.pop(user_id)

    leave_room(room=room_id)

    if not room.users_list and not room.spectators_list:
        # Delete the room if no users are left
        rooms.pop(room_id)
        close_room(room_id)
        socketio.emit('rooms_deleted', dataclasses.asdict(room), skip_sid=room_id)
        return

    # update room data and notify users
    socketio.emit('room_data_updated', dataclasses.asdict(room), to=room_id)
    socketio.emit('rooms_updated', dataclasses.asdict(room), skip_sid=room_id)
    socketio.emit('userLeft', { "sid": sid, "userId": user_id }, to=room_id)  # for conversations only


# ---------- CHAT ---------- #        

@socketio.on('sendMessage')
def handle_send_message(payload, bot=False):
    print(f"received message {payload}")
    message = payload['message']
    room_id = payload['roomId']
    user_id = payload.get('userId') # f"{request.remote_addr}"  # change to user_id when ready
    socketio.emit('receiveMessage', {'message': message, 'userId': user_id, 'bot': bot}, to=room_id)


class BotRoom:
    def __init__(self, room_id: int):
        self.room_id = room_id
        self.ready_time = 3 * 60  # Time in 3 minutes
        self.team_time = [60 * 5 , 60 * 5]  # Team 1 and Team 2 durations in seconds
        self.max_duration = 60 * 60  # Maximum duration of the room in seconds (1 hour)
        self.current_team = 1
        self.announce_time = 60
        self.start_time = time.time()
        self.last_action_time = time.time()
        self.state = 'waiting'  # Possible states: waiting, team_1, team_2, closing, closed

    def init_msg(self):
        handle_send_message(
            {'roomId': self.room_id,
            'message': 'Welcome! Debate will start in 3 minutes!',
            'userId': 'bot'},
            bot=True
        )

    def switch_bot_team(self) -> None:
        self.current_team = 3 - self.current_team
        self.last_action_time = time.time()
        self.announce_time = 60
        self.state = f'team_{self.current_team}'
        print(f"Room {self.room_id}: Now team {self.current_team}")

        # take the room's team names
        room = rooms.get(self.room_id, None)
        if room is None:
            return
        team_name = room.team_names[self.current_team - 1] # team_names is a list of 2 strings

        handle_send_message(
            {'message': f"Time's up! {team_name} team, your turn!",
            'userId': 'bot',
            'roomId': self.room_id},
            bot=True
        )

    def start_debate_msg(self) -> None:
        # take the room's team names
        room = rooms.get(self.room_id, None)
        if room is None:
            return
        team_name = room.team_names[self.current_team - 1] # team_names is a list of 2 strings

        handle_send_message(
            {'roomId': self.room_id,
            'message': f'Debate started! {team_name} team, your turn! You have 5 minutes!',
            'userId': 'bot'},
            bot=True
        )

    def close_room(self) -> None:
        print(f"Room {self.room_id}: bye")
        self.state = 'closed'

    def soon_close_msg(self) -> None:
        handle_send_message(
            {'roomId': self.room_id,
            'message': 'Debate will end in 10 minutes!',
            'userId': 'bot'},
            bot=True
        )

    def manage(self, current_time: float) -> None:
        elapsed_time = current_time - self.last_action_time
        total_elapsed_time = current_time - self.start_time
        # print(f"Room {self.room_id}: elapsed_time: {elapsed_time}, total_elapsed_time: {total_elapsed_time}, state: {self.state}")
        if self.state == 'waiting':
            if elapsed_time >= self.ready_time:
                self.state = f'team_{self.current_team}'
                self.last_action_time = current_time
                self.start_debate_msg()
                print(f"Room {self.room_id}: Now team {self.current_team}")
        elif self.state.startswith('team_'):
            team_duration = self.team_time[self.current_team - 1]
            if elapsed_time >= team_duration:
                self.switch_bot_team()
            elif elapsed_time >= self.announce_time:
                self.announce_time += 60
                room = rooms.get(self.room_id, None)
                if room is None:
                    return
                team_name = room.team_names[self.current_team - 1]
                handle_send_message(
                    {'roomId': self.room_id,
                    'message': f'{team_name} team, you have {round((team_duration - elapsed_time)/60)} minuets left!',
                    'userId': 'bot'},
                    bot=True
                )
            if self.max_duration - total_elapsed_time <= 5:
                self.soon_close_msg()
                self.state = 'closing'
                print(f"Room {self.room_id}: soon will be closed")
        elif total_elapsed_time >= self.max_duration:
            self.close_room()

class BotRoomManager:
    def __init__(self):
        self.rooms = {}
        self.to_remove = []

    def add_room(self, room_id) -> None:
        if room_id in self.rooms:
            print(f"Room {room_id} already exists.")
            return
        room = BotRoom(room_id)
        self.rooms[room_id] = room
        print(f"Room {room_id} added.")
        room.init_msg()

    def remove_room(self, id) -> None:
        if id not in self.rooms:
            # print(f"Room {id} does not exist.")
            return
        self.rooms.pop(id)
        print(f"Room {id} removed.")

    def run(self) -> None:
        while True:
            current_time = time.time()
            print("hello")
            rooms_copy = self.rooms.copy()
            for id, room in rooms_copy.items():
                if room.state == 'closed':
                    self.remove_room(id)
                else:
                    room.manage(current_time)
            eventlet.sleep(1)

bot_room_manager = BotRoomManager()
eventlet.spawn(bot_room_manager.run)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=8000, debug=True)
