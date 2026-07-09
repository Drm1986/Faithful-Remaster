# Faithful Remaster v11.10.22 — ملاحظات النشر بالعربي

هذه النسخة معمولة كـ **GitHub Release Package**، يعني الهدف منها مش إضافة workflow جديد، لكن تجهيز المشروع للنشر بشكل محترم وواضح قبل تصوير فيديو الإعلان.

## الهدف من النسخة

- تثبيت رقم النسخة على `v11.10.22` في `VERSION` والـ runtime.
- تجهيز `README.md` قوي كواجهة GitHub الرئيسية.
- إضافة tutorial كامل للمستخدم الجديد.
- إضافة release notes جاهزة.
- إضافة GitHub release body جاهز للنسخ.
- إضافة checklist قبل النشر.
- إضافة خطة فيديو showcase بعد ما الـ release يستقر.

## قبل ما تنشر على GitHub

1. افتح البرنامج وتأكد أن الرقم فوق هو:

```text
v11.10.22
```

2. تأكد أن اسم ملف الـ zip هو:

```text
Faithful-Remaster-v11.10.22-GitHub-Release-Tutorial-Windows.zip
```

3. جرّب profile صغير:

- Validate Profile
- Test ComfyUI
- Start Watching
- Process 3-5 textures
- Texture Manager auto-refresh

4. جرّب Batch Queue على لعبتين صغيرتين:

- Start Batch
- Skip to next game
- Previous game
- Stop Batch

5. لا ترفع الفيديو قبل ما تتأكد أن النسخة دي مستقرة فعلاً.

## ملفات مهمة

- `README.md`: واجهة المشروع على GitHub.
- `docs/GETTING_STARTED_TUTORIAL.md`: الشرح الكامل للمستخدم.
- `GITHUB_RELEASE_BODY_v11.10.22.md`: انسخه في صفحة GitHub Release.
- `RELEASE_NOTES_v11.10.22.md`: ملخص النسخة.
- `docs/GITHUB_RELEASE_CHECKLIST.md`: خطوات النشر.
- `docs/SHOWCASE_VIDEO_PLAN.md`: خطة الفيديو بعد النشر.

## صيغة النشر المقترحة

Release title:

```text
Faithful Remaster v11.10.22 — GitHub Release + Full Tutorial
```

Tag:

```text
v11.10.22
```

Assets:

```text
Faithful-Remaster-v11.10.22-GitHub-Release-Tutorial-Windows.zip
Faithful-Remaster-v11.10.22-GitHub-Release-Tutorial-Windows.zip.sha256.txt
```

## ملاحظة مهمة

هذه النسخة لا تغيّر الـ workflows ولا processing logic. هي مبنية على v11.10.21 وتضيف documentation/release readiness.
