# PDF Merger Utility

간단한 명령행 프로그램으로 여러 PDF를 지정한 순서대로 하나의 파일로 합칩니다. 테스트 코드와 함께 제공되어 있어 기능을 쉽게 검증할 수 있습니다.

## 설치

```bash
pip install -r requirements.txt
```

## 사용법

```bash
python pdf_merger.py merged.pdf input1.pdf input2.pdf [추가 PDF ...]
```

- `merged.pdf`: 생성할 출력 파일 경로입니다.
- `input*.pdf`: 병합할 PDF 파일들로, 전달한 순서 그대로 합쳐집니다.

병합 과정에서 암호화된 PDF를 만나면 오류를 반환하고 종료합니다.

## 테스트 실행

```bash
python -m pytest
```

임시로 생성한 PDF를 이용해 입력 검증과 병합 결과를 확인합니다.
