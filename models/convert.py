from ultralytics import YOLO

model = YOLO("model.pt")   # path ke file modelmu
model.export(format="onnx", imgsz=640)
