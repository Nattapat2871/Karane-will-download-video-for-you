import subprocess
import os
import json
import sys
import re # เพิ่มเข้ามาเพื่อ Parse output
import threading # เพิ่มเข้ามาเพื่อรันงานเบื้องหลัง
from urllib.parse import urlparse # ใช้สำหรับตรวจสอบ Domain
from flask import Flask, render_template, request, flash, redirect, url_for, jsonify # ไม่ใช้ session แล้วในเวอร์ชันนี้
from flask_socketio import SocketIO, emit # <--- เพิ่ม SocketIO และ emit

# --- Configuration ---
YT_DLP_EXECUTABLE = 'yt-dlp'

DOWNLOAD_FOLDER = "Downloads"
# รายชื่อ Domain ที่อนุญาต (จากโค้ดล่าสุดของคุณ)
ALLOWED_DOMAINS = {
    'youtube.com', 'youtu.be', 'tiktok.com', 'instagram.com',
    'facebook.com', 'fb.watch', 'x.com', 'twitter.com', 'bilibili.com', 'b23.tv',
}

# --- Flask App Setup ---
app = Flask(__name__)
# Secret key จำเป็นสำหรับ Flask session และ SocketIO (ควรตั้งให้ซับซ้อน)
app.secret_key = 'a_very_complex_and_random_secret_key_for_socketio'
# --- Initialize SocketIO ---
socketio = SocketIO(app)
# -------------------------

# --- Helper Function: ensure_download_folder ---
def ensure_download_folder():
    """Creates the download folder if it doesn't exist."""
    if not os.path.exists(DOWNLOAD_FOLDER):
        try:
            os.makedirs(DOWNLOAD_FOLDER)
            print(f"Created download folder: {DOWNLOAD_FOLDER}")
        except OSError as e:
            print(f"Error creating download folder {DOWNLOAD_FOLDER}: {e}")
            raise # Re-raise the exception

# --- ฟังก์ชันดาวน์โหลดเวอร์ชันใหม่ (ทำงานเบื้องหลังและส่ง Progress) ---
def download_video_with_progress(video_url, format_selection, output_path, sid):
    """Downloads video using Popen, parses progress, and emits SocketIO events."""
    ensure_download_folder()
    progress_data = { # สร้าง Dictionary สำหรับเก็บข้อมูล Progress
        'percent': 0.0,
        'downloaded_str': '0B',
        'total_str': 'N/A',
        'speed_str': '0B/s',
        'eta_str': 'N/A'
    }
    output_filename = "N/A" # ชื่อไฟล์เริ่มต้น

    output_template = os.path.join(output_path, '%(title)s [%(resolution)s - Karane will download video for you 💖].%(ext)s')

    command = [
        YT_DLP_EXECUTABLE,
        '-f', format_selection,
        '-o', output_template,
        '--no-check-certificates',
        '--no-mtime',
        '--progress', # <--- สั่งให้ yt-dlp แสดง progress
        '--newline',  # <--- ให้แต่ละ progress update ขึ้นบรรทัดใหม่
        video_url
    ]

    print(f"Starting download process for SID {sid}: {' '.join(command)}")

    process = None
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1
        )

        # --- ปรับปรุง Regex ให้ดึงข้อมูลได้มากขึ้น ---
        # ตัวอย่าง: [download]  15.3% of   12.94MiB at    1.01MiB/s ETA 00:11
        # ตัวอย่าง (เมื่อไม่ทราบขนาด): [download] Got server HTTP response: HTTP/1.1 200 OK
        # ตัวอย่าง (เมื่อไม่ทราบขนาด): [download]    1.56MiB added... or similar format
        progress_regex = re.compile(
            r"\[download\]\s+"
            r"(?P<percent>\d+\.?\d*)%\s+of\s+"  # Percent (กลุ่ม percent)
            r"(?P<total_str>~?\s*\d+\.?\d+\w*i?B)\s+at\s+" # Total size (กลุ่ม total_str) - เพิ่ม ~? และ \s*
            r"(?P<speed_str>\s*\d+\.?\d+\w*i?B/s)\s+" # Speed (กลุ่ม speed_str) - เพิ่ม \s*
            r"ETA\s+(?P<eta_str>\s*\d{2}:\d{2}(:\d{2})?)" # ETA (กลุ่ม eta_str) - เพิ่ม \s* และ (?:\d{2})? สำหรับชั่วโมง
        )
        # --- Regex สำหรับกรณีไม่ทราบขนาดไฟล์ทั้งหมด ---
        downloaded_only_regex = re.compile(
             r"\[download\]\s+(?P<downloaded_str>\d+\.?\d+\w*i?B)\s+in.*ETA\s+(?P<eta_str>\s*\d{2}:\d{2}(:\d{2})?)" # หรือรูปแบบอื่นที่ yt-dlp แสดง
             # หรืออาจจะเป็นรูปแบบนี้:
             # r"\[download\]\s+(?P<downloaded_str>\d+\.?\d+\w*i?B)\s+added"
        )
        # ----------------------------------------

        stdout_lines = []

        while True:
            line = process.stdout.readline()
            if not line:
                break

            stdout_lines.append(line.strip())
            print(f"[yt-dlp SID:{sid}] {line.strip()}")

            match = progress_regex.search(line)
            downloaded_match = downloaded_only_regex.search(line) # ลองหาแบบไม่มี total

            current_progress_data = progress_data.copy() # สำเนาข้อมูลเก่าไว้ก่อน

            if match:
                try:
                    current_progress_data['percent'] = float(match.group('percent'))
                    # คำนวณขนาดที่โหลดแล้วจาก % และขนาดทั้งหมด (อาจจะไม่ตรงเป๊ะ 100% แต่ใกล้เคียง)
                    # เราจะใช้ค่า downloaded_str จาก regex อื่นถ้ามี หรือจะแสดง total size แทน
                    current_progress_data['total_str'] = match.group('total_str').strip()
                    current_progress_data['speed_str'] = match.group('speed_str').strip()
                    current_progress_data['eta_str'] = match.group('eta_str').strip()
                    # สร้าง downloaded_str คร่าวๆ
                    # This is complex to calculate accurately without parsing units (MiB, KiB etc.)
                    # For simplicity, we'll rely on the display format below or potential 'downloaded_only_regex'
                    current_progress_data['downloaded_str'] = f"~{(current_progress_data['percent']/100.0):.1f} of {current_progress_data['total_str']}"

                except (ValueError, TypeError, AttributeError):
                    pass # ถ้าแปลงค่าไม่ได้ ก็ใช้ค่าเดิม

            elif downloaded_match: # ถ้าเจอรูปแบบที่บอกแค่ขนาดที่โหลด
                 try:
                     current_progress_data['downloaded_str'] = downloaded_match.group('downloaded_str').strip()
                     current_progress_data['eta_str'] = downloaded_match.group('eta_str').strip()
                     # Percent อาจจะไม่ทราบ หรืออาจจะต้องคงค่าเก่าไว้
                     # current_progress_data['percent'] = -1 # Indicate unknown percentage
                     current_progress_data['total_str'] = 'Unknown' # ไม่ทราบขนาดทั้งหมด
                     # Speed ก็อาจจะไม่ทราบจากบรรทัดนี้
                     current_progress_data['speed_str'] = 'N/A'
                 except (ValueError, TypeError, AttributeError):
                    pass

            # --- ส่งข้อมูลทั้งหมดผ่าน SocketIO ---
            if match or downloaded_match: # ส่งต่อเมื่อมีการเปลี่ยนแปลงจาก regex
                progress_data = current_progress_data # อัปเดตข้อมูลล่าสุด
                socketio.emit('download_progress', progress_data, room=sid)
                print(f"SID {sid} Progress: {progress_data}")
            # ------------------------------------

        process.stdout.close()
        return_code = process.wait()

        print(f"SID {sid} Process finished with code: {return_code}")

        if return_code == 0:
            # ... (โค้ดหาชื่อไฟล์ และ เปิดโฟลเดอร์ เดิม) ...
            final_dest_line = ""
            # ... (โค้ด Regex หา Destination เดิม) ...
            for l in reversed(stdout_lines):
                # ... (โค้ด search Regex เดิม) ...
                pass # ใส่ pass หรือโค้ดเดิม

            output_filename = os.path.basename(final_dest_line) if final_dest_line else "unknown file name"
            # ส่งสถานะสุดท้าย อาจจะส่งข้อมูลขนาดไฟล์ไปด้วยถ้าต้องการ
            socketio.emit('download_complete', {'status': 'Finished, yay 🤩!', 'filename': output_filename, 'final_size': progress_data.get('total_str', 'N/A')}, room=sid) #

            # --- โค้ดเปิดโฟลเดอร์ (เหมือนเดิม) ---
            try:
                 download_folder_path = os.path.abspath(DOWNLOAD_FOLDER)
                 print(f"Download complete. Attempting to open folder: {download_folder_path}")
                 if sys.platform == "win32":
                     os.startfile(download_folder_path)
                     print("Opened folder on Windows.")
                 elif sys.platform == "darwin":
                     subprocess.Popen(["open", download_folder_path])
                     print("Opened folder on macOS.")
                 else:
                     subprocess.Popen(["xdg-open", download_folder_path])
                     print("Opened folder using xdg-open (Linux).")
            except FileNotFoundError:
                 print(f"Could not open folder automatically: '{download_folder_path}' not found.")
            except Exception as e:
                 print(f"Could not open folder automatically: {e}")
            # -------------------------------

        else:
            error_output = "\n".join(stdout_lines[-15:])
            socketio.emit('download_error', {'message': f"yt-dlp ล้มเหลว (code {return_code}):\n{error_output}"}, room=sid)

    except FileNotFoundError:
         print(f"Error: '{YT_DLP_EXECUTABLE}' command not found.")
         socketio.emit('download_error', {'message': f"ไม่พบโปรแกรม '{YT_DLP_EXECUTABLE}'"}, room=sid)
    except Exception as e:
        print(f"Error during download process for SID {sid}: {e}")
        if process and process.poll() is None:
             process.terminate()
        socketio.emit('download_error', {'message': f"เกิดข้อผิดพลาดในระบบ: {e}"}, room=sid)

# --- Flask Routes ---
@app.route('/', methods=['GET'])
def index():
    """Displays the main download form."""
    return render_template('index.html') # ใช้ index.html ล่าสุด

# --- Route สำหรับ Get Video Info ---
# ใช้โค้ดจากไฟล์ล่าสุดของคุณ แต่เพิ่มการตรวจสอบ Domain
@app.route('/get_video_info', methods=['POST'])
def get_video_info():
    video_url = request.json.get('video_url')
    if not video_url:
        return jsonify({'error': 'Missing video URL'}), 400

    # --- เพิ่มการตรวจสอบ Domain ---
    try:
        parsed_url = urlparse(video_url)
        hostname = parsed_url.hostname
        if hostname:
             if hostname.startswith('www.'): hostname = hostname[4:]
             is_allowed = any(hostname == domain or hostname.endswith('.' + domain) for domain in ALLOWED_DOMAINS if domain != hostname)
             if not is_allowed and hostname not in ALLOWED_DOMAINS:
                   print(f"Info check blocked for domain: {hostname}")
                   return jsonify({'error': f"Not supported URL From : {hostname}"}), 400
        else:
             return jsonify({'error': 'Invalid URL'}), 400
    except Exception as e:
        print(f"Error parsing URL for info: {e}")
        return jsonify({'error': 'The URL format is invalid.'}), 400
    # ---------------------------

    # โค้ด get_video_info เดิมจากไฟล์ล่าสุด
    command = [ YT_DLP_EXECUTABLE, '-j', '--no-check-certificates', video_url ]
    print(f"Fetching info: {' '.join(command)}")
    try:
        process = subprocess.run(
            command, capture_output=True, text=True, check=True,
            encoding='utf-8', errors='replace'
        )
        metadata = json.loads(process.stdout)
        title = metadata.get('title', 'N/A')
        thumbnail_url = metadata.get('thumbnail')
        print(f"Info fetched: Title='{title}', Thumbnail='{thumbnail_url}'")
        return jsonify({'title': title, 'thumbnail': thumbnail_url})
    except FileNotFoundError:
        print(f"Error: '{YT_DLP_EXECUTABLE}' command not found.")
        return jsonify({'error': f"ไม่พบโปรแกรม '{YT_DLP_EXECUTABLE}'"}), 500
    except subprocess.CalledProcessError as e:
        print(f"yt-dlp error fetching info: {e.stderr or e.stdout}")
        error_output = e.stderr.strip() or e.stdout.strip()
        if error_output.startswith("ERROR:"): error_output = error_output[len("ERROR:"):].strip()
        return jsonify({'error': f"ดึงข้อมูลไม่ได้: {error_output}"}), 400
    except json.JSONDecodeError:
        print(f"Error decoding JSON from yt-dlp output.")
        return jsonify({'error': 'ไม่สามารถอ่านข้อมูล metadata ที่ได้จาก yt-dlp'}), 500
    except Exception as e:
        print(f"Unexpected error fetching info: {e}")
        return jsonify({'error': f'เกิดข้อผิดพลาดไม่คาดคิด: {e}'}), 500

# --- Route เดิมสำหรับเริ่มดาวน์โหลด (ปรับปรุงเพื่อใช้กับ SocketIO) ---
# Route นี้ตอนนี้ไม่ได้ใช้เริ่มโหลดโดยตรง แต่ยังคงมีไว้เผื่อการ submit form แบบเดิม (ซึ่ง JS ป้องกันไว้)
# หรืออาจจะลบทิ้งไปเลยก็ได้ถ้ามั่นใจว่าใช้ SocketIO อย่างเดียว
@app.route('/start_download', methods=['POST'])
def start_download_route():
    # โค้ดนี้จะทำงานถ้า JS มีปัญหาและเกิดการ Submit Form แบบปกติ
    # ควรจะแจ้งเตือนผู้ใช้ว่าให้ใช้ผ่าน Interface ปกติ
    flash("เกิดข้อผิดพลาด โปรดลองเริ่มดาวน์โหลดผ่านปุ่มหน้าเว็บ", "error")
    return redirect(url_for('index'))

# --- SocketIO Event Handlers ---
@socketio.on('connect')
def handle_connect():
    """เมื่อ Client เชื่อมต่อ SocketIO สำเร็จ"""
    print(f'Client connected: {request.sid}')

@socketio.on('disconnect')
def handle_disconnect():
    """เมื่อ Client ตัดการเชื่อมต่อ"""
    print(f'Client disconnected: {request.sid}')

@socketio.on('start_download_event') # Event ที่ Client ส่งมาเพื่อเริ่มโหลด
def handle_start_download(data):
    """เริ่มกระบวนการดาวน์โหลดใน Background Thread เมื่อได้รับ Event จาก Client"""
    sid = request.sid
    video_url = data.get('url')
    format_choice = data.get('format') # รับ format choice จาก client
    print(f"Received start download event from SID {sid} for URL: {video_url} with format choice: {format_choice}")

    if not video_url:
        emit('download_error', {'message': 'ไม่ได้ระบุ URL'}, room=sid)
        return

    # --- ตรวจสอบ Domain อีกครั้งที่นี่ เพื่อความปลอดภัย ---
    try:
        parsed_url = urlparse(video_url)
        hostname = parsed_url.hostname
        if hostname:
            if hostname.startswith('www.'): hostname = hostname[4:]
            is_allowed = any(hostname == d or hostname.endswith('.' + d) for d in ALLOWED_DOMAINS if d != hostname)
            if not is_allowed and hostname not in ALLOWED_DOMAINS:
                 print(f"Download blocked by Socket event for domain: {hostname}")
                 emit('download_error', {'message': f"ไม่อนุญาตให้ดาวน์โหลดจาก: {hostname}"}, room=sid)
                 return
        else:
            emit('download_error', {'message': "รูปแบบ URL ไม่ถูกต้อง"}, room=sid)
            return
    except Exception as e:
        print(f"Error parsing URL in socket handler: {e}")
        emit('download_error', {'message': "ไม่สามารถตรวจสอบรูปแบบ URL ได้"}, room=sid)
        return
    # ---------------------------------------------

    # --- เลือก Format String (ใช้ Logic จากไฟล์ล่าสุดของคุณ) ---
    format_string = ""
    if format_choice == '1': format_string = "bestvideo+bestaudio/best"
    elif format_choice == '2': format_string = "bestvideo[height<=?2160]+bestaudio/best[height<=?2160]"
    elif format_choice == '3': format_string = "bestvideo[height<=?1440]+bestaudio/best[height<=?1440]"
    elif format_choice == '4': format_string = "bestvideo[height<=?1080]+bestaudio/best[height<=?1080]"
    elif format_choice == '5': format_string = "bestvideo[height<=?720]+bestaudio/best[height<=?720]"
    elif format_choice == '6': format_string = "bestvideo[height<=?480]+bestaudio/best[height<=?480]" # เพิ่ม 6 กลับมาตาม index.html
    elif format_choice == '7': format_string = "best[ext=mp4]/best"
    elif format_choice == '8': format_string = "bestaudio/best"
    else: format_string = "bestvideo+bestaudio/best" # Default
    # ----------------------------------------------------

    if not format_string:
         emit('download_error', {'message': 'Format ที่เลือกไม่ถูกต้อง'}, room=sid)
         return

    # --- เริ่ม Thread ใหม่สำหรับดาวน์โหลด ---
    print(f"Starting download thread for SID {sid} with format: {format_string}")
    thread = threading.Thread(target=download_video_with_progress,
                              args=(video_url, format_string, DOWNLOAD_FOLDER, sid))
    thread.daemon = True # ตั้งเป็น Daemon thread เพื่อให้ปิดตามโปรแกรมหลัก
    thread.start()
    # -----------------------------------

    # Emit บอกกลับไปว่าเริ่มแล้ว
    emit('download_started', {'message': 'เริ่มกระบวนการดาวน์โหลดแล้ว...'}, room=sid)


# --- Run the App with SocketIO ---
if __name__ == '__main__':
    ensure_download_folder()
    print("Starting Flask-SocketIO server...")
    # allow_unsafe_werkzeug=True อาจจำเป็นสำหรับ Werkzeug เวอร์ชันใหม่ๆ กับ debug mode
    socketio.run(app, debug=True, host='127.0.0.1', port=5000, allow_unsafe_werkzeug=True)