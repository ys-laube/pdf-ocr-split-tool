# PDF OCR Tool — OCR / 병합 / 분할

대용량 한글·영문 전공서적 PDF를 페이지 단위로 분석해서, 저해상도로 OCR된 페이지만 골라 원하는 DPI로 재처리하는 GUI 프로그램입니다. PDF 병합과 북마크 기반 분할도 같이 합니다.

---

## 🚀 새 Windows PC에 원클릭 설치

PowerShell을 **관리자 권한 필요 없이** 열고 한 줄만 실행하세요:

```powershell
irm https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/install.ps1 | iex
```

(`YOUR_USERNAME/YOUR_REPO`는 GitHub 레포 주소로 교체)

이 명령이 자동으로 처리하는 것:

1. ✅ **winget** 확인 (Windows 10/11 기본 탑재)
2. ✅ **Python 3.12** 설치
3. ✅ **Tesseract OCR** 설치
4. ✅ **Korean traineddata** (`kor.traineddata`) 다운로드
5. ✅ **Ghostscript** 설치
6. ✅ 프로그램 코드 GitHub에서 다운로드 → `C:\Users\<사용자>\PDF-OCR-Tool\`
7. ✅ Python 패키지 자동 설치 (`pip install -r requirements.txt`)
8. ✅ **바탕화면 바로가기 자동 생성**

설치 끝나면 바탕화면의 "PDF OCR Tool" 아이콘 더블클릭 → GUI 실행.

### winget이 없는 경우 (구형 Windows)

Microsoft Store에서 "App Installer"를 먼저 설치하면 winget이 같이 들어옵니다:
https://apps.microsoft.com/detail/9NBLGGH4NNS1

---

## 사용 방법

GUI 창 상단에 **3개 탭** — 원하는 작업 탭으로 이동.

### 🔍 OCR 탭

1. PDF 드래그&드롭 또는 `[파일 선택]`
2. 출력 폴더 지정 (원본과 다른 폴더 권장)
3. 임계값 `[a] DPI`와 목표 `[b] DPI` 선택 (300 / 450 / 600)
4. `[📊 페이지 분석만]` 으로 먼저 OCR 필요 페이지 수 확인 (권장)
5. `[▶ OCR 시작]` 클릭
6. 진행 중에 **전체 진행률 / 경과 / 예상 총 / 남은 시간** 실시간 표시
7. 출력 파일명: `원본이름_ocr_[목표DPI]dpi.pdf`

### 📑 PDF 병합 탭

- 여러 PDF를 한 번에 드래그&드롭 또는 `[파일 추가]`
- 리스트에서 ↑↓ 버튼으로 순서 조정
- 출력 위치 지정 → `[▶ 병합 시작]`

### ✂ PDF 분할 (북마크 기준) 탭

- PDF 1개 드롭 → 북마크 자동 인식
- 분할 기준 레벨(1/2/3) 선택
- 출력 폴더 지정 → `[▶ 분할 시작]`
- 각 출력 파일이 북마크 이름 그대로 저장 (illegal char는 `_`로 치환)

---

## 동작 원리

- **OCR 검출**: 페이지 텍스트 + content stream의 `3 Tr` 연산자(invisible text mode)로 OCR 레이어 판별
- **DPI 측정**: PDF의 임베디드 이미지 픽셀 크기 / 표시 영역(인치)
- **선택적 재OCR**: OCRmyPDF의 `--pages` + `--redo-ocr` + `--oversample [목표DPI]` 옵션으로 필요한 페이지만 처리, 나머지는 그대로 통과
- **용량 최적화**: `--optimize 1` (무손실)
- **시간 추정**: 첫 실행은 기본 모델, 이후 실행마다 실제 소요시간과 비교해 `시스템 보정 N.NNx`를 학습 (사용자 홈의 `~/.pdf-ocr-tool/timing_history.json`)
- **절전 방지**: OCR 중 Windows 절전·대기 차단 (`SetThreadExecutionState` API)
- **콘솔창 숨김**: Ghostscript / Tesseract 자식 프로세스 `CREATE_NO_WINDOW` 강제 주입

---

## 파일 구성

| 파일 | 역할 |
| --- | --- |
| `pdf_ocr_gui.py` | 메인 GUI (탭, 이벤트 핸들러, 진행률 관리) |
| `ocr_core.py` | PDF 페이지 분석 + OCRmyPDF 호출 |
| `pdf_tools.py` | 병합, 분할, 북마크 검출, 파일명 정제 |
| `progress_plugin.py` | OCRmyPDF 진행률 후크 플러그인 |
| `progress_bridge.py` | GUI ↔ 플러그인 사이 큐 공유 모듈 |
| `timing_stats.py` | 적응형 시간 예측 (사용자 홈에 학습 기록 저장) |
| `requirements.txt` | Python 의존성 목록 |
| `install.ps1` | Windows 원클릭 설치 스크립트 (winget 기반) |
| `setup.bat` | (수동 설치용) 외부 도구 검사 + Python 패키지 설치 |
| `run.bat` | Windows에서 GUI 실행 (pythonw → python 폴백) |

---

## Mac 지원 (현재 상태)

코드는 **Mac에서도 동작 가능하도록 준비**되어 있습니다 (폰트 폴백, 절전 방지의 `caffeinate` 분기 포함). 단:
- Mac용 자동 설치 스크립트(`install.sh`)는 **아직 미작성**
- Mac에서 직접 사용하려면 수동으로 다음을 설치:
  ```bash
  brew install python tesseract tesseract-lang ghostscript
  pip3 install -r requirements.txt
  python3 pdf_ocr_gui.py
  ```

Mac 자동화가 필요해지면 알려주세요.

---

## 알려진 한계 / 주의사항

- 페이지 분류는 휴리스틱(이미지가 페이지의 60% 이상 덮는지) 기반 — 디지털 텍스트와 큰 일러스트가 섞인 페이지가 잘못 분류될 수 있음. 의심되면 먼저 `[📊 페이지 분석만]`
- `--redo-ocr` 모드는 "보이는 디지털 텍스트" 페이지에 오류를 냄. 분석에서 `digital`로 분류된 페이지는 자동 제외, 휴리스틱이 틀린 경우 OCR이 실패할 수 있음
- 200MB 이상의 책 한 권 처리 시 수 분 ~ 수십 분 (페이지 수 × DPI에 비례)
- **임시 디스크 공간**: 처리 중 `%TEMP%`에 페이지를 래스터화한 이미지를 보관. 1000페이지짜리 책을 600dpi로 처리하면 수 GB 일시 사용
- **DPI에 대한 오해**: 파일명의 `_600dpi`는 OCR 처리 해상도일 뿐, PDF 안 이미지의 DPI는 OCR 후에도 그대로 유지됨
- `[⏹ 중단]`은 강제 중단이며 임시 파일이 남을 수 있음
- **Windows Update 자동 재부팅**은 OS 레벨이라 우리 도구가 막을 수 없음 — 작업 전에 Windows 설정의 "활성 시간"을 확인

---

## 라이선스

MIT — `LICENSE` 파일 참조.

내부에서 사용하는 외부 프로그램들은 각자의 라이선스를 따릅니다:
- **Tesseract OCR**: Apache 2.0
- **Ghostscript**: AGPL (개인 사용 무료, 상업 배포는 별도 라이선스 필요)
- **OCRmyPDF**: MPL 2.0
- **PyMuPDF**: AGPL (대체로 무료 사용 가능, 상업적 통합은 별도 라이선스 권장)
