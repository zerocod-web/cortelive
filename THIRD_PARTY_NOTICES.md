# Avisos de terceiros

## OpenShorts — MIT
https://github.com/mutonby/openshorts — Copyright (c) mutonby. Licença MIT.
Ideias reaproveitadas (reescritas, não copiadas linha a linha):
- `subtitles.py` → juntar fragmentos de palavra do faster-whisper (`cortelive/fala.py: juntar_fragmentos`) e
  legenda ASS com a palavra ativa destacada.
- `clip_selection.py` → prender início/fim do corte em pausas entre palavras (`snap_clip_to_words`) e não
  soltar cortes sobrepostos (`dedupe_overlapping`) — em `cortelive/momento.py` e `cortelive/cli.py`.

> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
> documentation files (the "Software"), to deal in the Software without restriction, including without limitation
> the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and
> to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above
> copyright notice and this permission notice shall be included in all copies or substantial portions of the
> Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.

Nenhum código AGPL (Medusa Clip etc.) foi usado.

## Fontes
- Anton e Montserrat — SIL Open Font License 1.1 (`cortelive/fontes/Montserrat-OFL.txt`).
- Outfit — Copyright 2021 The Outfit Project Authors (https://github.com/Outfitio/Outfit-Fonts), SIL Open Font License 1.1 (`painel/fontes/Outfit-OFL.txt`, fonte variável local `painel/fontes/Outfit-Variable.woff2`).

## whisper.cpp — MIT (binário em `ferramentas/whispercpp`, baixado do release oficial).

## YuNet (detector de rosto da câmera) — MIT
`cortelive/modelos_cam/face_detection_yunet_2023mar.onnx` (~230 KB), do repositório opencv_zoo (https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet). YuNet: Copyright (c) 2020 Shiqi Yu (https://github.com/ShiqiYu/libfacedetection.train). Licença MIT. O arquivo vai dentro do pacote e roda em CPU pelo OpenCV (`cv2.FaceDetectorYN`); nada é baixado na instalação.
