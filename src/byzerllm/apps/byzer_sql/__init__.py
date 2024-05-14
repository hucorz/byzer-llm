from typing import Dict
from dataclasses import asdict
from byzerllm.utils.client import ByzerLLM
import json

import byzerllm
import os
from loguru import logger
from typing import Optional
from os.path import expanduser
from byzerllm.apps.llama_index import get_service_context,get_storage_context
from pydantic import BaseModel
from llama_index.core.service_context import ServiceContext
from llama_index.core.storage import StorageContext
from byzerllm import ByzerLLM,ByzerRetrieval
from dataclasses import dataclass

@dataclass
class Env:
    llm:ByzerLLM
    retrieval:Optional[ByzerRetrieval] = None
    namespace:str    
    default_storage_context:Optional[StorageContext] = None
    default_service_context: ServiceContext   
     

def _connect(
        default_model:str,
        default_emb_model:str,        
        ray_address:str="auto",
        base_dir:Optional[str]=None,
        storage_version:Optional[str]=None):

    version = storage_version or "0.1.11"        
    home = expanduser("~")        
    base_dir = base_dir or os.path.join(home, ".auto-coder")

    storage_enabled = True

    if not os.path.exists(base_dir):
        logger.info(f"Byzer Storage not found in {base_dir}")
        base_dir = os.path.join(home, ".byzerllm")

    if not os.path.exists(base_dir):
        logger.info(f"Byzer Storage not found in {base_dir}")
        storage_enabled = False

    code_search_path = None        
    if storage_enabled and default_emb_model:
        logger.info(f"Byzer Storage found in {base_dir}")            
        libs_dir = os.path.join(base_dir, "storage", "libs", f"byzer-retrieval-lib-{version}")        
        code_search_path = [libs_dir]
        logger.info(f"Connect and start Byzer Retrieval version {version}") 

    if storage_enabled and not default_emb_model:
        logger.warning("Byzer Storage is detected but default_emb_model is not set, The storage will be disabled.")           
        storage_enabled = False

    env_vars = byzerllm.connect_cluster(address=ray_address,code_search_path=code_search_path)
    
    retrieval = None
    if storage_enabled:
        retrieval = ByzerRetrieval()
        retrieval.launch_gateway()
        
    llm = byzerllm.ByzerLLM()
    if default_model:
        llm.setup_default_model_name(default_model)
    if default_emb_model:    
        llm.setup_default_emb_model_name(default_emb_model)
    return llm,retrieval

def prepare_env(default_model:Optional[str]=None,
                default_emb_model:Optional[str]=None,
                namespace:str="default",**kargs)->Env:
    llm,retrieval = _connect(default_model=default_model,default_emb_model=default_emb_model,**kargs)    
    service_context = get_service_context(llm=llm)
    default_storage_context = None
    if retrieval:
        default_storage_context = get_storage_context(llm=llm,retrieval=retrieval,chunk_collection=namespace,namespace=namespace)
    
    return Env(llm=llm,
            retrieval=retrieval,
            namespace=namespace,
            default_storage_context=default_storage_context,
            default_service_context=service_context)

def chat(ray_context):   
    conf = ray_context.conf()
    udf_name = conf["UDF_CLIENT"] 
    
    input_value = [json.loads(row["value"]) for row in ray_context.python_context.fetch_once_as_rows()]
    
    llm = ByzerLLM()
    llm.setup_template(model=udf_name,template="auto")
    llm.setup_default_emb_model_name("emb")
    llm.setup_default_model_name(udf_name)
    llm.setup_extra_generation_params(udf_name,extra_generation_params={
        "temperature":0.01,
        "top_p":0.99
    })
    
    result = []
    for value in input_value:
        v = value.get("query",value.get("instruction",""))
        history = json.loads(value.get("history","[]"))
        
        for key in ["query","instruction","history","user_role","system_msg","assistant_role"]:
            value.pop(key,"")            
                
        conversations = history + [{
            "role":"user",
            "content":v
        }]
        t = llm.chat_oai(conversations=conversations,llm_config={**value})

        response = asdict(t[0])
        
        new_history =  history + [{
            "role":"user",
            "content":v
        }] + [{
            "role":"assistant",
            "content":response["output"]
        }]  

        response["history"] = new_history    
        
        result.append({"value":[json.dumps(response,ensure_ascii=False)]})
    
    ray_context.build_result(result) 


def deploy(infer_params:str,conf:Dict[str,str]):
    '''
    !byzerllm setup single;
    !byzerllm setup "num_gpus=4";
    !byzerllm setup "resources.master=0.001";
    run command as LLM.`` where 
    action="infer"
    and pretrainedModelType="llama"
    and localModelDir="/home/byzerllm/models/openbuddy-llama-13b-v5-fp16"
    and reconnect="true"
    and udfName="llama_13b_chat"
    and modelTable="command";
    '''
    infer_params = json.loads(infer_params)
    llm = ByzerLLM()
    num_gpus = int(conf.get("num_gpus",1))
    num_workers = int(conf.get("maxConcurrency",1))

    pretrained_model_type = infer_params.get("pretrainedModelType","custom/auto")
    model_path = infer_params.get("localModelDir","")
    
    infer_params.pop("pretrainedModelType","")
    infer_params.pop("localModelDir","")
    infer_params.pop("udfName","")
    infer_params.pop("modelTable","")
    infer_params.pop("reconnect","")


    chat_name = conf["UDF_CLIENT"]
    
    llm.setup_num_workers(num_workers).setup_gpus_per_worker(num_gpus)

    llm.deploy(model_path=model_path,
            pretrained_model_type=pretrained_model_type,
            udf_name=chat_name,
            infer_params={
               **infer_params
            })            


