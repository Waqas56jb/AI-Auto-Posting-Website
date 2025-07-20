from flask import Flask, request, jsonify, render_template
import os
import whisper
from werkzeug.utils import secure_filename
import ffmpeg
from pydub import AudioSegment
import tempfile

app = Flask(__name__)

# Configure folders
UPLOAD_FOLDER = 'static/videos'
TRIMMED_FOLDER = 'static/trimmed'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(TRIMMED_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'mp4', 'mov'}

# Initialize Whisper model
model = whisper.load_model("base")

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    return render_template('editing.html')

@app.route('/api/upload_video', methods=['POST'])
def upload_video():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        return jsonify({'filename': filename})
    return jsonify({'error': 'Invalid file format'}), 400

@app.route('/api/trim_video', methods=['POST'])
def trim_video():
    data = request.get_json()
    filename = data.get('filename')
    duration = data.get('duration')
    start = data.get('start')
    end = data.get('end')
    is_trimmed = data.get('is_trimmed', False)
    if not filename:
        return jsonify({'error': 'No filename provided'}), 400

    input_folder = TRIMMED_FOLDER if is_trimmed else UPLOAD_FOLDER
    input_path = os.path.join(input_folder, filename)
    if not os.path.exists(input_path):
        return jsonify({'error': 'Video not found'}), 404

    try:
        probe = ffmpeg.probe(input_path)
        video_duration = float(probe['format']['duration'])
        clips = []

        if duration:  # Auto trim based on audio
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_audio:
                ffmpeg.input(input_path).output(temp_audio.name, format='wav').run(quiet=True)
                result = model.transcribe(temp_audio.name, language='en')
                os.unlink(temp_audio.name)

            segments = sorted(result['segments'], key=lambda x: x['avg_logprob'], reverse=True)[:4]
            for i, seg in enumerate(segments[:4]):
                start_time = max(0, seg['start'])
                end_time = min(start_time + float(duration), video_duration)
                if end_time > start_time:
                    output_filename = f"clip_{i+1}_{filename}"
                    output_path = os.path.join(TRIMMED_FOLDER, output_filename)
                    ffmpeg.input(input_path, ss=start_time, t=end_time - start_time).output(
                        output_path, c='copy', format='mp4'
                    ).run(quiet=True, overwrite_output=True)
                    clips.append(output_filename)
        else:  # Manual trim
            start = max(0, float(start))
            end = min(float(end), video_duration)
            if end <= start:
                return jsonify({'error': 'Invalid start or end time'}), 400
            output_filename = f"manual_clip_{filename}"
            output_path = os.path.join(TRIMMED_FOLDER, output_filename)
            ffmpeg.input(input_path, ss=start, t=end - start).output(
                output_path, c='copy', format='mp4'
            ).run(quiet=True, overwrite_output=True)
            clips.append(output_filename)

        return jsonify({'clips': clips})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/add_text', methods=['POST'])
def add_text():
    data = request.get_json()
    filename = data.get('filename')
    text = data.get('text')
    if not filename or not text:
        return jsonify({'error': 'Filename and text required'}), 400

    input_path = os.path.join(TRIMMED_FOLDER, filename)
    if not os.path.exists(input_path):
        return jsonify({'error': 'Video not found'}), 404

    try:
        output_filename = f"text_{filename}"
        output_path = os.path.join(TRIMMED_FOLDER, output_filename)
        stream = ffmpeg.input(input_path)
        stream = ffmpeg.drawtext(
            stream,
            text=text,
            fontfile='arial.ttf',  # Assumes Arial is available; adjust if needed
            fontsize=24,
            fontcolor='white',
            x='(w-tw)/2',
            y='h-50',
            box=1,
            boxcolor='black@0.5',
            boxborderw=5
        )
        stream.output(output_path, c='libx264', preset='fast').run(quiet=True, overwrite_output=True)
        return jsonify({'filename': output_filename})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)