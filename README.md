# CCTV DETEKSI KEMACETAN (YOLO)


## install & dependencies

- [DOWNLOAD PYTHON V3.11.0](https://www.python.org/downloads/release/python-3110)
- [DOWNLOAD NODEJS V20.20.0^](https://nodejs.org/en)
- [FFMPEG built with gcc 11.2.0](https://drive.google.com/file/d/1oY415KsA8uFA1KCFtyBY3jgXG0fZchOO/view?usp=drive_link)

## dependencies
- nodejs
```bash
npm install || npm i
```
- python
```bash
pip install -r requirements.txt || python -m pip install -r requirements.txt
pip install torch==2.4.1 torchvision==0.19.1 --index-url https://download.pytorch.org/whl/cpu
```

## Run project
- nodejs
``` bash
npm run dev || npm run dev -- --max-old-space-size=2024
```
- python
``` bash
python app.py || py app.py
```
