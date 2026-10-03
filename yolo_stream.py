from ultralytics import YOLO

model = YOLO("yolo11n.pt")  # downloads on first run
for r in model.predict("http://campi.local:8000/stream.mjpg", stream=True, device=0, show=True):
    pass