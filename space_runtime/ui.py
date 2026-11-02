from __future__ import annotations


def make_app(library):
    import gradio as gr
    def lookup(payload):
        try:return library.lookup(payload)
        except ValueError as error:raise gr.Error(str(error)) from None
    def generate(payload):
        try:return library.generate(payload)
        except Exception as error:
            message=str(error).casefold()
            if 'drifted' in message or 'species-appropriate' in message:
                return {'status':'voice_unavailable','message':'Не удалось выдержать голос персонажа. Запись не опубликована; повторите позже.'}
            if 'quota' in message or 'exceeded' in message or 'gpu duration' in message:
                return {'status':'quota','message':'Лимит бесплатной генерации исчерпан. Готовые записи остаются доступны; повторите позже.'}
            import logging
            logging.getLogger(__name__).error('Generation failed (%s)',type(error).__name__)
            return {'status':'failed','message':'Не удалось создать запись. Повторите позже.'}
    # JSON-only API: no file-path, Audio or FileExplorer input/output components.
    with gr.Blocks(title='Warcraft Audi') as app:
        gr.Markdown('# Warcraft Audi\nСервис озвучки квестов. Игроку достаточно программы Windows; эту страницу открывать не требуется.')
        payload=gr.JSON(visible=False)
        result=gr.JSON(visible=False)
        gr.Button('Lookup',visible=False).click(lookup,inputs=payload,outputs=result,api_name='lookup',concurrency_limit=8)
        gr.Button('Generate',visible=False).click(generate,inputs=payload,outputs=result,api_name='generate',concurrency_limit=1)
    return app.queue(max_size=16,default_concurrency_limit=1)
