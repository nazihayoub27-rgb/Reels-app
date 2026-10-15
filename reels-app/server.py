#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ريلزي — خادم الوسيط للتنزيل من منصات التواصل
التشغيل:
    pip install fastapi uvicorn yt-dlp
    uvicorn server:app --host 0.0.0.0 --port 8000
ثم افتح: http://localhost:8000
"""
import os, re, json, time, glob, uuid, socket, secrets, tempfile, shutil
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

MAX_BYTES = 500 * 1024 * 1024  # حد أقصى 500 م.ب لكل فيديو

# بدون ffmpeg لا يمكن دمج الصوت والفيديو، فنطلب ملفًا واحدًا جاهزًا؛ ومع ffmpeg نستعمل أفضل جودة
if shutil.which("ffmpeg"):
    FORMAT = "bv*[height<=720]+ba/b[height<=720]/b"
else:
    FORMAT = "b[height<=720]/b/best"

app = FastAPI(title="Reelsy Downloader")


def cleanup(d):
    shutil.rmtree(d, ignore_errors=True)


@app.post("/download")
def download(url: str = Query(..., description="رابط الفيديو")):
    tmp = tempfile.mkdtemp(prefix="reelsy_")
    base = os.path.join(tmp, str(uuid.uuid4()))
    opts = {
        "format": FORMAT,
        "merge_output_format": "mp4",
        "outtmpl": base + ".%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "max_filesize": MAX_BYTES,
        "socket_timeout": 60,
        "restrictfilenames": True,
    }
    # إذا وُجد ملف cookies.txt بجانب server.py نستعمله (ضروري لإنستغرام غالبًا)
    ck = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
    if os.path.isfile(ck):
        opts["cookiefile"] = ck
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = (info.get("title") or "video").strip()
    except Exception as e:
        cleanup(tmp)
        msg = str(e).split("\n")[-1][:200]
        if "No video formats" in str(e) or "login" in str(e).lower():
            msg = ("لا يوجد فيديو في هذا الرابط (ربما منشور صور)، "
                   "أو إنستغرام يطلب تسجيل الدخول: استعمل رابط /reel/ "
                   "أو أضف ملف cookies.txt")
        raise HTTPException(status_code=422, detail=f"تعذّر التنزيل: {msg}")

    files = [f for f in glob.glob(base + "*") if os.path.isfile(f)]
    if not files:
        cleanup(tmp)
        raise HTTPException(status_code=422, detail="لم يُعثر على ملف الفيديو بعد التنزيل.")
    path = max(files, key=os.path.getsize)
    if os.path.getsize(path) > MAX_BYTES:
        cleanup(tmp)
        raise HTTPException(status_code=413, detail="الفيديو أكبر من 500 م.ب.")

    from fastapi.background import BackgroundTasks
    # الحذف بعد الإرسال
    resp = FileResponse(path, media_type="video/mp4",
                        filename=(title[:60] or "video") + ".mp4")
    # نخزن مسار المجلد في الهيدر داخليًا ونحذف عبر مكالمة لاحقة بسيطة
    resp.headers["X-Tmp-Dir"] = tmp
    return resp


@app.post("/cleanup")
def cleanup_dir(tmp: str = Query(...)):
    # نحذف فقط مجلدات أنشأناها نحن (بادئة reelsy_)
    if os.path.basename(tmp).startswith("reelsy_"):
        cleanup(tmp)
    return {"ok": True}



# ======== مشاركة فيديو بين الأجهزة عبر QR (شبكة محلية، بدون إنترنت) ========
SHARE_DIR = os.path.join(tempfile.gettempdir(), "reelsy_share")
SHARE_TTL = 60 * 60          # الرمز صالح ساعة واحدة
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{6,24}$")
os.makedirs(SHARE_DIR, exist_ok=True)


def purge_shares():
    now = time.time()
    for f in glob.glob(os.path.join(SHARE_DIR, "*")):
        try:
            if now - os.path.getmtime(f) > SHARE_TTL:
                os.remove(f)
        except OSError:
            pass


def share_paths(token):
    if not TOKEN_RE.match(token or ""):
        raise HTTPException(status_code=404, detail="رمز غير صالح.")
    return (os.path.join(SHARE_DIR, token + ".bin"),
            os.path.join(SHARE_DIR, token + ".json"))


@app.get("/lan-ip")
def lan_ip():
    ips = []

    def add(ip):
        if ip and not ip.startswith(("127.", "169.254.", "0.")) and ip not in ips:
            ips.append(ip)

    try:  # عنوان الشبكة الفعلية (لا يُرسل أي بيانات)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for ai in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(ai[4][0])
    except Exception:
        pass
    return {"ips": ips}


@app.post("/share")
async def share_upload(request: Request, name: str = Query("video.mp4"), cat: str = Query("")):
    purge_shares()
    token = secrets.token_urlsafe(6)
    bin_path, meta_path = share_paths(token)
    size = 0
    try:
        with open(bin_path, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(status_code=413, detail="الفيديو أكبر من 500 م.ب.")
                f.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="لم يصل أي ملف.")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"name": name[:120], "category": cat[:60], "size": size}, f, ensure_ascii=False)
    except HTTPException:
        for p in (bin_path, meta_path):
            if os.path.exists(p):
                os.remove(p)
        raise
    return {"token": token, "size": size}


@app.get("/share/{token}/info")
def share_info(token: str):
    bin_path, meta_path = share_paths(token)
    if not (os.path.isfile(bin_path) and os.path.isfile(meta_path)):
        raise HTTPException(status_code=404, detail="انتهت صلاحية الرمز أو حُذف الفيديو.")
    with open(meta_path, encoding="utf-8") as f:
        return json.load(f)


@app.get("/share/{token}/file")
def share_file(token: str):
    bin_path, _ = share_paths(token)
    if not os.path.isfile(bin_path):
        raise HTTPException(status_code=404, detail="انتهت صلاحية الرمز أو حُذف الفيديو.")
    return FileResponse(bin_path, media_type="application/octet-stream")

# تقديم التطبيق والسو نفس الأصل (شرط أساسي لعمل Service Worker)
app.mount("/", StaticFiles(directory=".", html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
