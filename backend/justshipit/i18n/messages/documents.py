"""Files the owner attaches (`services/documents.py`, `services/doc_extract.py`, `api/documents.py`): why a file was
not kept, and the refusals around attaching and removing one."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "documents.refused.empty": {"en": "This file is empty.", "ko": "빈 파일이에요."},
    "documents.refused.too_big": {"en": "Files over {max} MB can't be attached. Split it or upload a smaller copy.",
                                  "ko": "{max}MB가 넘는 파일은 넣을 수 없어요. 나누거나 더 작은 파일로 넣어 주세요."},
    "documents.refused.unknown_type": {
        "en": "Only PDF, Word (.docx), PowerPoint (.pptx), Markdown and text files can be attached.",
        "ko": "PDF, 워드(.docx), 파워포인트(.pptx), 마크다운, 텍스트 파일만 넣을 수 있어요."},
    "documents.refused.mismatch": {"en": "The name says {ext}, but the contents aren't. Upload the original file.",
                                   "ko": "이름은 {ext} 파일인데 내용은 아니에요. 원본 파일을 넣어 주세요."},
    "documents.refused.scan": {"en": "This looks like a scan with no text. Upload a version with text, or paste the "
                                     "content.",
                               "ko": "글자를 읽을 수 없는 스캔본이에요. 텍스트가 있는 파일이나 내용을 붙여 넣어 주세요."},
    "documents.refused.encrypted": {"en": "This PDF has a password. Upload a copy without one.",
                                    "ko": "암호가 걸린 PDF예요. 암호를 푼 파일을 넣어 주세요."},
    "documents.refused.broken": {"en": "This file looks damaged, so it couldn't be read. Save it again and upload the "
                                       "new copy.",
                                 "ko": "파일이 손상돼서 읽지 못했어요. 다시 저장해서 넣어 주세요."},
    "documents.refused.zip_bomb": {"en": "This file unpacks to far more than its size, so it wasn't opened.",
                                   "ko": "압축을 풀면 지나치게 커지는 파일이라 열지 않았어요."},
    "documents.refused.no_text": {"en": "There's no text in this file.", "ko": "읽을 글자가 없는 파일이에요."},
    "documents.refused.too_many": {"en": "A business holds up to {max} files. Remove one to add another.",
                                   "ko": "파일은 한 사업에 {max}개까지 넣을 수 있어요. 하나를 빼고 넣어 주세요."},
    "documents.refused.no_name": {"en": "The file has no name.", "ko": "파일 이름이 없어요."},

    "documents.not_found": {"en": "Couldn't find file #{id}.", "ko": "없는 파일이에요(#{id})."},
    "documents.taken": {"en": "File #{id} already belongs to another business.",
                        "ko": "다른 사업에 이미 넣은 파일이에요(#{id})."},
    "documents.no_business": {"en": "Couldn't find business B-{id}.", "ko": "없는 사업이에요(B-{id})."},
}
