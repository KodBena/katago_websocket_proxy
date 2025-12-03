
# LLM generated

import asyncio, json, subprocess, uuid, sys, logging
from collections import deque
import websockets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

KATAGO_MODEL_FILE = "/path/to/katann/wonderful.bin.gz"
KATAGO_PATH="/path/to/katago_trt2"
KATAGO_CFG="/path/to/analysis.cfg"
KATAGO_CMD = [KATAGO_PATH, "analysis", "-config", KATAGO_CFG, "-model", KATAGO_MODEL_FILE,  "-quit-without-waiting"]
HOST = "127.0.0.1"
PORT = 41949
ID_SEPARATOR = "|||"

clients = {}
client_queries = {}

def filtered_dict(dict):
    return { k:dict[k] for k in dict if k not in ['moveInfos','ownership','policy','rootInfo']}

async def client_handler(ws):
    client_id = uuid.uuid4().hex
    clients[client_id] = ws
    client_queries[client_id] = {}
    
    logger.info(f"Client {client_id} connected")
    
    try:
        async for raw in ws:
            print(f"recvd raw[{client_id}: {raw}")
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON from client: {e}")
                await ws.send(json.dumps({"error": f"Invalid JSON: {e}"}))
                continue
            
            if "id" not in msg:
                logger.warning(f"Received query without ID: {msg}")
                await ws.send(json.dumps({"error": "Query must have 'id' field"}))
                continue

            if 'action' in msg:
                if msg['action'] == 'terminate':
                    msg['terminateId'] = f"{client_id}{ID_SEPARATOR}{msg['terminateId']}"

            orig_id = msg["id"]
            
            prefixed_id = f"{client_id}{ID_SEPARATOR}{orig_id}"
            msg["id"] = prefixed_id
            
            client_queries[client_id][orig_id] = prefixed_id

            try:
                print(f"send[{client_id}]: {msg}")
                proc.stdin.write(json.dumps(msg).encode() + b'\n')
                await proc.stdin.drain()
            except Exception as e:
                logger.error(f"Failed to write to KataGo stdin: {e}")
                await ws.send(json.dumps({"error": f"KataGo engine failed to accept input: {e}"}))

    except websockets.exceptions.ConnectionClosedOK:
        logger.info(f"Client {client_id} disconnected normally")
    except websockets.exceptions.ConnectionClosedError as e:
        logger.warning(f"Client {client_id} disconnected unexpectedly: {e}")
    finally:
        clients.pop(client_id, None)
        client_queries.pop(client_id, None)


async def katago_reader(proc):
    
    line_buffer = b''
    
    while True:
        try:
            chunk = await proc.stdout.read(65536)
            
            if not chunk:
                break

            line_buffer += chunk

            while b'\n' in line_buffer:
                line_bytes, line_buffer = line_buffer.split(b'\n', 1)
                
                line = line_bytes.decode('utf-8').strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as e:
                    logger.warning(f"KataGo sent non-JSON output: {line} - {e}")
                    continue
                
                print(f"recvd[kg]: {filtered_dict(msg)}")
                rid = msg.get("id")
                if not rid or ID_SEPARATOR not in rid:
                    logger.warning(f"Response missing valid ID: {msg}")
                    continue
                
                client_id, orig_id = rid.split(ID_SEPARATOR, 1)
                msg["id"] = orig_id
                if 'terminateId' in msg:
                    client_term_id, orig_term_id = msg['terminateId'].split(ID_SEPARATOR,1)
                    msg['terminateId'] = orig_term_id
                
                if msg.get("isDuringSearch") == False and "turnNumber" in msg:
                    if client_id in client_queries:
                        client_queries[client_id].pop(orig_id, None)
                
                ws = clients.get(client_id)
                if ws:
                    try:
                        await ws.send(json.dumps(msg))
                    except Exception as e:
                        logger.error(f"Failed to send to client {client_id}: {e}")
                else:
                    logger.debug(f"Response for disconnected client {client_id}")
            
        except Exception as e:
            logger.error(f"Error in katago_reader: {e}")
            break

async def main():
    global proc
    proc = await asyncio.create_subprocess_exec(
        *KATAGO_CMD,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=sys.stderr,
    )

    asyncio.create_task(katago_reader(proc))

    async with websockets.serve(client_handler, HOST, PORT):
        logger.info(f"KataGo Proxy Server running on ws://{HOST}:{PORT}")
        await asyncio.Future()  # Run forever

if __name__ == "__main__":
    try:
        proc = None
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server stopped manually.")
    finally:
        if proc and proc.returncode is None:
            proc.terminate()
            logger.info("KataGo process terminated.")
