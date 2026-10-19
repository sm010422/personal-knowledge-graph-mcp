# ADR-0008: 이미지 배포는 CI가 매니페스트의 태그를 커밋하는 방식

- 상태: Accepted
- 날짜: 2026-10-19

## 맥락

같은 클러스터의 기존 앱(target-tracking-service, threat-intel-ai-service)은 이렇게 배포한다.

- CI가 `:latest`와 `:<sha>`를 push한다.
- ArgoCD Image Updater가 `:latest`의 digest 변화를 감지해서, 인프라 레포에 `.argocd-source-*.yaml`을 write-back한다.

이 프로젝트는 매니페스트가 앱 레포(`k8s/`)에 있다. Image Updater 방식을 그대로 쓰려면 이 레포에 대한 쓰기 권한(deploy key)을 클러스터 Secret으로 추가해야 한다.

## 선택지

1. **Image Updater + git write-back**: 기존과 일관된다. 클러스터에 레포별 쓰기 자격증명이 하나 더 생긴다. `:latest` digest 추적이라 매니페스트만 봐서는 어떤 커밋이 떠 있는지 바로 알기 어렵다.
2. **Image Updater + `argocd` write-back**: 자격증명은 필요 없다. 하지만 이미지 태그가 git이 아니라 ArgoCD Application 파라미터에만 남아, GitOps 원칙(git = 원하는 상태)이 깨진다.
3. **CI가 직접 태그 커밋**: 빌드가 끝나면 같은 워크플로가 `k8s/kustomization.yaml`의 `newTag`를 커밋 SHA로 바꿔 커밋한다(`GITHUB_TOKEN`, `contents: write`).

## 결정

3번이다. 태그는 항상 전체 커밋 SHA다.

## 결과

- `k8s/kustomization.yaml`만 보면 클러스터에 떠 있는 정확한 커밋을 알 수 있다. 롤백은 그 파일을 이전 SHA로 되돌리는 커밋 하나다.
- 클러스터에 이 레포용 추가 자격증명이 없다.
- 무한 루프 방지: 태그 커밋 메시지에 `[skip ci]`를 넣고, 워크플로 트리거도 `server/**` 경로로 제한한다(`k8s/` 변경은 빌드를 일으키지 않는다).
- 단점: 배포마다 레포에 봇 커밋이 하나씩 쌓인다.
- 기존 앱과 방식이 다르다는 점은 [k3s-msa-infrastructure 쪽 문서](https://github.com/sm010422/k3s-msa-infrastructure/blob/main/docs/Personal-KG-MCP-Deployment.md)에도 적어 두었다.
