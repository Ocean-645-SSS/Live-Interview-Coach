from liverag.rag.filenames import decode_transport_filename
from liverag.rag.server import _safe_filename


def test_decodes_percent_encoded_multipart_filename() -> None:
    encoded = "%E5%AD%A6%E4%B9%A0%E8%B7%AF%E7%BA%BF.docx"

    assert decode_transport_filename(encoded) == "学习路线.docx"
    assert _safe_filename(encoded) == "学习路线.docx"


def test_decodes_legacy_underscore_hex_filename() -> None:
    encoded = "_E5_AD_A6_E4_B9_A0_E8_B7_AF_E7_BA_BF.docx"

    assert decode_transport_filename(encoded) == "学习路线.docx"


def test_does_not_corrupt_normal_underscore_filename() -> None:
    assert decode_transport_filename("report_AB_CD.docx") == "report_AB_CD.docx"
