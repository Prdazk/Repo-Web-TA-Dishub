# CCTV DETEKSI ARUS LALU LINTAS (YOLO)
Sistem monitoring Arus Lalu lintas berbasis computer vision menggunakan Yolov8 untuk mendeteksi, melacak, dan menghitung kendaraan secara real-time melalui cctv. sistem ini di lengkapi Dashboard monitoring berbasis web serta menyimpan data menggunakan SQlite 

> Screenshot aplikasi akan ditambahkan di sini.

## Install & Dependencies

- [DOWNLOAD PYTHON V3.10.11](https://www.python.org/downloads/release/python-31011/)
- [DOWNLOAD NODEJS V24.13.0](https://nodejs.org/en)
- [FFMPEG built with gcc 11.2.0](https://drive.google.com/file/d/1oY415KsA8uFA1KCFtyBY3jgXG0fZchOO/view?usp=drive_link)

## Dependencies

- nodejs
```bash
npm install || npm i
```

- python
```bash
pip install -r requirements.txt || python -m pip install -r requirements.txt
pip install torch==2.4.1 torchvision==0.19.1 --index-url https://download.pytorch.org/whl/cpu
```

## Run Project

- nodejs
```bash
npm run dev || npm run dev -- --max-old-space-size=2024
```

- python
```bash
python app.py || py app.py
```
