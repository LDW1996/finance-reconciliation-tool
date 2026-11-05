"""Mail matching endpoints; manager state is obtained at request time."""
from fastapi import HTTPException, Query, Response
from . import mail_tasks as tasks


def register(router):

    @router.get('/matching')
    def matching(scope: str=Query('unidentified',pattern='^(unidentified|unclosed|all)$'),page: int=Query(1,ge=1),
                 page_size: int=Query(50,ge=1,le=100),minimum_score: int=Query(0,ge=0,le=100),
                 day_window: int=Query(3,ge=1,le=7),candidate_limit: int=Query(3,ge=1,le=20),
                 keyword: str=Query('',max_length=200),sort_by: str=Query('source',pattern='^(source|score)$')):
        return tasks.manager().workspace.matching(scope,page,page_size,minimum_score,day_window,candidate_limit,keyword,sort_by)

    @router.get('/matching/state')
    def matching_state(day_window: int=Query(3,ge=1,le=7)):
        state=tasks.manager().workspace.match_state(day_window)
        state['review_revision']=tasks.manager().store.get_state('review_revision','0')
        return state

    @router.post('/matching/retry')
    def retry_matching(day_window: int=Query(3,ge=1,le=7)):
        return tasks.manager().workspace.match_state(day_window,retry=True)

    @router.get('/matching/snapshot')
    def matching_snapshot(scope: str=Query('unidentified',pattern='^(unidentified|unclosed|all)$'),page: int=Query(1,ge=1),
                          page_size: int=Query(50,ge=1,le=100),minimum_score: int=Query(0,ge=0,le=100),
                          day_window: int=Query(3,ge=1,le=7),candidate_limit: int=Query(3,ge=1,le=20),
                          keyword: str=Query('',max_length=200),sort_by: str=Query('source',pattern='^(source|score)$'),
                          review_filter: str=Query('pending',pattern='^(all|pending|clues|no_clues|confirmed)$')):
        return tasks.manager().workspace.matching_snapshot(scope=scope,page=page,page_size=page_size,minimum_score=minimum_score,
            day_window=day_window,candidate_limit=candidate_limit,keyword=keyword,sort_by=sort_by,review_filter=review_filter)

    @router.post('/matching/review')
    def review(body: tasks.ReceiptReview):
        try: return tasks.manager().workspace.review(body.ledger_id,body.receipt_id,body.payment_id,body.state,body.day_window,body.expected_signature)
        except ValueError as error: raise HTTPException(409,str(error)) from None

    @router.get('/matching/cashier-package')
    def cashier_package(day_window: int=Query(3,ge=1,le=7)):
        from .cashier_export import build_cashier_package
        from starlette.background import BackgroundTask
        from fastapi.responses import FileResponse
        try:path=build_cashier_package(tasks.manager().workspace,day_window)
        except ValueError as error:raise HTTPException(400,str(error)) from None
        return FileResponse(path,media_type='application/zip',filename='cashier-review.zip',
                            background=BackgroundTask(path.unlink,missing_ok=True))

    @router.get('/matching/export')
    def export(scope: str=Query('unidentified',pattern='^(unidentified|unclosed|all)$'),minimum_score: int=Query(0,ge=0,le=100),day_window: int=Query(3,ge=1,le=7)):
        from urllib.parse import quote
        try: raw,name=tasks.manager().workspace.export(scope,minimum_score,day_window)
        except ValueError as error: raise HTTPException(400,str(error)) from None
        return Response(raw,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition':f"attachment; filename*=UTF-8''{quote(name)}"})
