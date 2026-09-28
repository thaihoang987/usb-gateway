# USB Gateway

USB Gateway la Docker app cho Unraid, quan ly nhieu cong USB serial tu mot
Web UI:

- Modbus TCP sang Modbus RTU bang mot tien trinh `mbusd` cho moi USB.
- Raw TCP sang UART cho Arduino, JSON/text va giao thuc binary rieng.
- Quet `/dev/serial/by-id` va `/dev/serial/by-path`.
- Luu cau hinh tai `/mnt/user/appdata/usb-gateway/config.json`.
- Tu khoi dong lai gateway sau khi Docker hoac Unraid khoi dong.

Tac gia: [thaihoang987](https://github.com/thaihoang987)

## Cai tren Unraid bang template

Chay mot lan trong Unraid Terminal:

```bash
mkdir -p /boot/config/plugins/dockerMan/templates-user
wget -O /boot/config/plugins/dockerMan/templates-user/my-usb-gateway.xml \
  https://raw.githubusercontent.com/thaihoang987/usb-gateway/master/unraid/my-usb-gateway.xml
```

Sau do vao **Docker -> Add Container**, chon template `usb-gateway`, kiem tra
cac gia tri va bam **Apply**. Image
`ghcr.io/thaihoang987/usb-gateway:latest` se duoc tu dong tai ve.

Web UI mac dinh: `http://IP_UNRAID:8098`.

Icon cua template dung file:
`/mnt/user/App_Custom/Icon_app/usb manager.png`.

## Cai bang script

```bash
git clone https://github.com/thaihoang987/usb-gateway.git /tmp/usb-gateway
cd /tmp/usb-gateway
sh install-unraid.sh
```

Script tai image, tao thu muc appdata va cai template DockerMan. Script khong
dung, xoa hoac sua container gateway hien co.

Neu muon build image truc tiep tren Unraid:

```bash
BUILD_LOCAL=1 IMAGE_NAME=ghcr.io/thaihoang987/usb-gateway:latest sh install-unraid.sh
```

## Chay truc tiep

```bash
docker run -d \
  --name usb-gateway \
  --network host \
  --privileged \
  --restart unless-stopped \
  -e WEB_PORT=8098 \
  -e CONFIG_PATH=/config/config.json \
  -v /mnt/user/appdata/usb-gateway:/config \
  -v /dev/serial:/dev/serial:ro \
  ghcr.io/thaihoang987/usb-gateway:latest
```

## Su dung an toan

Khong bat cung mot USB trong hai container hoac dich vu cung luc. Khi chuyen
tu gateway cu, hay tao gateway moi tren mot USB chua duoc su dung, test TCP tu
Web UI, sau do moi dung dich vu cu tuong ung.

Khong dung TCP port `8888` neu `mbusd-gateway` hien tai van dang nghe port do.
Co the thu bang `8890`, `8891`, ... truoc.

Raw mode truyen byte nguyen ban va khong phan tich noi dung. DTR va RTS mac
dinh tat de han che Arduino tu reset khi cong serial duoc mo.

## Phat trien

```bash
docker compose up --build
python -m unittest discover -s tests -v
```

Chi tiet thiet ke nam trong [ARCHITECTURE.md](ARCHITECTURE.md).
