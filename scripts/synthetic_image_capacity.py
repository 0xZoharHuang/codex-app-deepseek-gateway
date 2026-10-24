"""Pure synthetic capacity fixture; generates bytes in memory and never calls a network API."""
from __future__ import annotations
import base64,hashlib,json,random,struct,zlib

GLYPHS={
'0':['01110','10001','10011','10101','11001','10001','01110'],
'1':['00100','01100','00100','00100','00100','00100','01110'],
'2':['01110','10001','00001','00010','00100','01000','11111'],
'3':['11110','00001','00001','01110','00001','00001','11110'],
'4':['00010','00110','01010','10010','11111','00010','00010'],
'5':['11111','10000','10000','11110','00001','00001','11110'],
'6':['00110','01000','10000','11110','10001','10001','01110'],
'7':['11111','00001','00010','00100','01000','01000','01000'],
'8':['01110','10001','10001','01110','10001','10001','01110'],
'9':['01110','10001','10001','01111','00001','00010','01100'],
'A':['01110','10001','10001','11111','10001','10001','10001'],
'B':['11110','10001','10001','11110','10001','10001','11110'],
'C':['01111','10000','10000','10000','10000','10000','01111'],
'D':['11110','10001','10001','10001','10001','10001','11110'],
'E':['11111','10000','10000','11110','10000','10000','11111'],
'F':['11111','10000','10000','11110','10000','10000','10000']}

def chunk(kind,data):return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)

def make_fixture(seed=2026102301, target_bytes=34009154):
 rng=random.Random(seed);width,height=400,210
 codes=[''.join(rng.choice('0123456789ABCDEF') for _ in range(6)) for _ in range(2)]
 images=[]
 for index in range(101):
  pixels=bytearray(rng.randbytes(width*height*3))
  if index in {0,100}:
   text=codes[0 if index==0 else 1]
   for y in range(55,140):
    pixels[(y*width+55)*3:(y*width+345)*3]=b'\xff'*(290*3)
   scale=6
   for char_index,char in enumerate(text):
    for gy,row in enumerate(GLYPHS[char]):
     for gx,bit in enumerate(row):
      if bit=='1':
       for dy in range(scale):
        y=75+gy*scale+dy;x=85+(char_index*6+gx)*scale
        pixels[(y*width+x)*3:(y*width+x+scale)*3]=b'\0'*(scale*3)
  scanlines=b''.join(b'\0'+pixels[y*width*3:(y+1)*width*3] for y in range(height))
  image=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(scanlines,1))+chunk(b'IEND',b'')
  images.append(image)
 history=[{'type':'message','role':'user','content':[{'type':'input_image','detail':'original','image_url':'data:image/png;base64,'+base64.b64encode(image).decode()}]} for image in images]
 history.extend({'type':'message','role':'user','content':[{'type':'input_text','text':'Synthetic transport context item '+str(i)}]} for i in range(1104))
 prompt='Inspect all attached images in their given order. The first and the last images each contain a white banner with a six-character uppercase hexadecimal code. Reply only FIRSTCODE,LASTCODE, reading the actual letters and digits from those two banners. Do not infer or invent a code. All other context is synthetic transport padding.'
 history.append({'type':'message','role':'user','content':[{'type':'input_text','text':prompt}]})
 payload={'model':'deepseek/deepseek-v4.1-flash-1017','provider':{'only':['morph/fp8'],'allow_fallbacks':False,'require_parameters':True},'input':history,'reasoning':{'effort':'low'},'max_output_tokens':2048,'stream':False,'store':False}
 raw=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
 deficit=target_bytes-len(raw)
 if deficit<0:raise ValueError('Fixture base exceeds selected exact byte budget')
 history[-2]['content'][0]['text']+='x'*deficit
 raw=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode();assert len(raw)==target_bytes
 manifest={'seed':seed,'request_bytes':len(raw),'request_sha256':hashlib.sha256(raw).hexdigest(),'history_items':len(history),'image_count':len(images),'image_dimensions':[width,height],'unique_images':len({hashlib.sha256(i).digest() for i in images}),'original_image_bytes':sum(map(len,images)),'text_padding_bytes':deficit,'expected_visual_output':','.join(codes),'expected_output_is_not_in_text_prompt':','.join(codes) not in prompt,'images':[{'index':i,'bytes':len(image),'sha256':hashlib.sha256(image).hexdigest()} for i,image in enumerate(images)],'requested_provider':'morph/fp8','max_output_tokens':2048,'network_requests':0}
 return payload,manifest

if __name__=='__main__':
 _,manifest=make_fixture();print(json.dumps(manifest,indent=2))
