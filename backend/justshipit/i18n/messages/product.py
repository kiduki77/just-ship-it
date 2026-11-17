"""A product the owner already has (`services/existing_product.py`, `engineering/current_state.py`): the import's
refusals, the kinds of secret the scan names, and the lines the server writes into the plan for it."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "product.import.need_source": {"en": "Give the folder your product's git repository is in.",
                                   "ko": "제품의 git 저장소가 있는 폴더를 적어 주세요."},
    "product.import.github_not_live": {
        "en": "Importing from a GitHub link isn't supported yet. Clone the repository on this PC, then give its "
              "folder.",
        "ko": "GitHub 링크로는 아직 가져올 수 없어요. 이 PC에 저장소를 내려받은 뒤 그 폴더를 적어 주세요."},
    "product.import.not_absolute": {"en": "Give the full path, from the drive on (for example C:/work/my-app).",
                                    "ko": "드라이브부터 전체 경로를 적어 주세요(예: C:/work/my-app)."},
    "product.import.not_found": {"en": "There's no folder at {path}.", "ko": "이 경로에 폴더가 없어요: {path}"},
    "product.import.not_git": {"en": "{path} isn't a git repository. Give the folder that holds your product's .git.",
                               "ko": "git 저장소가 아니에요: {path}. 제품의 .git 폴더가 있는 곳을 적어 주세요."},
    "product.import.not_top": {"en": "That folder is inside a repository. Give the repository's top folder: {top}",
                               "ko": "저장소 안쪽 폴더예요. 저장소 맨 위 폴더를 적어 주세요: {top}"},
    "product.import.inside_products": {
        "en": "That folder is one of the company's own copies under products/. Give the folder you work in.",
        "ko": "그 폴더는 products/ 안에 있는 회사 사본이에요. 오너님이 직접 작업하는 폴더를 적어 주세요."},
    "product.import.no_commits": {"en": "The repository has no commits yet. Commit your code first, then import it.",
                                  "ko": "저장소에 커밋이 아직 없어요. 코드를 먼저 커밋한 뒤 가져와 주세요."},
    "product.import.taken": {
        "en": "products/{repo} already exists and came from somewhere else. Rename your folder, then try again.",
        "ko": "products/{repo} 폴더가 이미 있고, 다른 곳에서 가져온 거예요. 폴더 이름을 바꾼 뒤 다시 해 주세요."},
    "product.import.in_use": {
        "en": "B-{id} already works on products/{repo}. Carry on there, or archive it first.",
        "ko": "B-{id} 사업이 이미 products/{repo} 사본으로 일하고 있어요. 그 사업에서 이어 가거나 먼저 보관해 주세요."},
    "product.import.branch_name": {
        "en": "The branch checked out in that repository has a name the company can't work with. Check out a branch "
              "with a plain name such as main, then import it again.",
        "ko": "그 저장소에서 지금 열려 있는 브랜치 이름으로는 회사가 일할 수 없어요. main처럼 평범한 이름의 브랜치로 바꾼 뒤 "
              "다시 가져와 주세요."},
    "product.import.clone_failed": {"en": "Couldn't copy the repository. Check that git can read it, then try again.",
                                    "ko": "저장소를 복사하지 못했어요. git으로 읽을 수 있는지 확인하고 다시 해 주세요."},
    "product.import.secret": {
        "en": "Not imported: {where} looks like it holds {kind}{more}. Take it out of git, history included, then "
              "import again.",
        "ko": "가져오지 않았어요. 비밀 정보로 보이는 내용이 있어요({kind}): {where}{more}. git에서 기록까지 지운 뒤 "
              "다시 가져와 주세요."},
    "product.import.where_line": {"en": "{path}, line {line}", "ko": "{path} {line}번째 줄"},
    "product.import.more": {"en": {"one": " (and {n} more place)", "other": " (and {n} more places)"},
                            "ko": " 외 {n}곳"},
    "product.import.detached": {
        "en": "The repository isn't on a branch (a detached HEAD). Check out the branch the company should start "
              "from, then import again.",
        "ko": "저장소가 브랜치에 있지 않아요(detached HEAD). 회사가 시작할 브랜치로 바꾼 뒤 다시 가져와 주세요."},
    "product.import.scan_failed": {
        "en": "Couldn't check the repository for secrets, so nothing was imported. Try again in a moment.",
        "ko": "저장소에 비밀 정보가 있는지 확인하지 못해서 가져오지 않았어요. 잠시 뒤 다시 해 주세요."},
    "product.import.where_commit": {"en": "{path}, in commit {commit}", "ko": "{path}(커밋 {commit})"},

    "product.secret.env_file": {"en": "a committed .env file", "ko": "커밋한 .env 파일"},
    "product.secret.private_key": {"en": "a private key", "ko": "개인 키"},
    "product.secret.github_token": {"en": "a GitHub token", "ko": "GitHub 토큰"},
    "product.secret.slack_token": {"en": "a Slack token", "ko": "Slack 토큰"},
    "product.secret.api_key": {"en": "an API key", "ko": "API 키"},
    "product.secret.aws_key": {"en": "an AWS key", "ko": "AWS 키"},
    "product.secret.google_key": {"en": "a Google API key", "ko": "Google API 키"},
    "product.secret.token": {"en": "a token", "ko": "토큰"},

    "product.brief.imported": {
        "en": "An existing product: the company starts from your code on branch {branch} at commit {commit}, "
              "copied to products/{repo}. Your original folder isn't touched.",
        "ko": "이미 있는 제품이에요. 회사는 {branch} 브랜치의 커밋 {commit} 코드에서 시작하고, products/{repo}에 "
              "복사해 두었어요. 원래 폴더는 건드리지 않아요."},
    "product.brief.state": {"en": "Where it stands, from the product agent's reading of the code: {summary}",
                            "ko": "제품기획 에이전트가 코드를 읽고 본 지금 상태: {summary}"},
    "product.read_again.not_failed": {
        "en": "The code isn't marked as unread, so there is nothing to read again.",
        "ko": "코드를 읽지 못한 상태가 아니라서 다시 읽을 게 없어요."},
    "product.activity.read_again": {"en": "The owner had the product agent read products/{repo} again",
                                    "ko": "오너님이 제품기획 에이전트에게 products/{repo} 코드를 다시 읽게 했어요"},
    "product.activity.reported": {"en": "The product agent read products/{repo} and wrote where the product stands",
                                  "ko": "제품기획 에이전트가 products/{repo} 코드를 읽고 지금 상태를 정리했어요"},
}
