from space_runtime.bootstrap import build

app=build()
app.launch(server_name='0.0.0.0',server_port=7860)
