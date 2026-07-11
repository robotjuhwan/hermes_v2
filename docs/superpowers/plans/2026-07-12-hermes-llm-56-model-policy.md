# HERMES Jue GPT-5.6 적용 계획

1. 역할별 모델 정책과 operation override 계약을 테스트로 고정한다.
2. AppSettings에 critical/reasoning/utility/offline 설정을 추가하고 기존 alias를 유지한다.
3. 모든 Codex runtime 생성부가 중앙 정책을 사용하도록 연결한다.
4. workflow manifest와 운영 상태 표시를 유효 모델에 맞춘다.
5. 실제 SDK에서 Sol/Terra/Luna 최소 호출을 검증한다.
6. 집중·도메인·전체 검증 후 관련 런너를 재시작한다.
7. 재시작 뒤 프로세스, readiness, 신규 LLM 텔레메트리를 확인한다.
