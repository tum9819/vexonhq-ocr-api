"""Compatibility tombstones for the retired WHT/P.N.D. workflow.

VEXONHQ does not track an actual withholding event or an actual remittance.
Inferring withholding from an expense category made full-paid musician fees and
rent look as though tax had been withheld and paid.  Keep the historic routes
registered so old bookmarks and integrations receive an explicit HTTP 410
instead of a misleading empty report or a silent 404.
"""

from typing import NoReturn, Optional

from fastapi import APIRouter, HTTPException, Query


router = APIRouter()

WHT_DECOMMISSION_DETAIL = (
    "ยกเลิกโมดูลภาษีหัก ณ ที่จ่าย/ภ.ง.ด. ใน VEXONHQ แล้ว "
    "เนื่องจากระบบไม่ได้บันทึกการหักหรือนำส่งจริง"
)


def raise_wht_decommissioned() -> NoReturn:
    """Return an unambiguous tombstone for every retired WHT export."""
    raise HTTPException(status_code=410, detail=WHT_DECOMMISSION_DETAIL)


@router.get("/tax/wht-summary")
def wht_summary(
    month: Optional[str] = Query(None, description="YYYY-MM"),
    branch_code: str = Query("thawi_watthana"),
):
    """Retired: VEXONHQ must not infer withholding from expense categories."""
    del month, branch_code
    raise_wht_decommissioned()


@router.get("/tax/wht-export")
def wht_export(
    month: Optional[str] = Query(None, description="YYYY-MM"),
    branch_code: str = Query("thawi_watthana"),
):
    """Retired: no WHT workbook is generated from unverified tax events."""
    del month, branch_code
    raise_wht_decommissioned()
