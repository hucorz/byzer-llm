from transformers import AutoTokenizer, AutoModelForCausalLM,BitsAndBytesConfig,StoppingCriteriaList
import transformers
import torch
from typing import Dict,List,Tuple
from byzerllm.utils import (generate_instruction_from_history,
compute_max_new_tokens,tokenize_stopping_sequences,StopSequencesCriteria)
import os
import time

def stream_chat(self,tokenizer,ins:str, his:List[Dict[str,str]]=[],  
        max_length:int=4090, 
        top_p:float=0.95,
        temperature:float=0.1,**kwargs):
        
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu") 
    timeout_s = float(kwargs.get("timeout_s",60*5)) 
    skip_check_min_length = int(kwargs.get("stopping_sequences_skip_check_min_length",0))       
    
    role_mapping = {        
        "user":"User",        
        "assistant":"Assistant",
    }
    
    fin_ins = generate_instruction_from_history(ins,his,role_mapping=role_mapping)     

    tokens = tokenizer(fin_ins, return_token_type_ids=False,return_tensors="pt").to(device)

    stopping_criteria = None
    
    if "stopping_sequences" in kwargs:        
        stopping_sequences = [torch.tensor(word).to(device) for word in tokenize_stopping_sequences(tokenizer,kwargs["stopping_sequences"].split(","))]    
        input_length = tokens["input_ids"].shape[1]
        stopping_criteria=StoppingCriteriaList([StopSequencesCriteria(
            tokenizer=tokenizer,
            stops=stopping_sequences,
            input_start=input_length,
            skip_check_min_length=skip_check_min_length
            )])
    
    max_new_tokens = compute_max_new_tokens(tokens,max_length)   
    
    start_time = time.monotonic()        
    response = self.generate(
        input_ids=tokens["input_ids"],
        max_new_tokens= max_new_tokens,
        repetition_penalty=1.05,
        temperature=temperature,
        attention_mask=tokens.attention_mask,
        eos_token_id=tokenizer.eos_token_id,
        pad_token_id=tokenizer.eos_token_id,
        bos_token_id=tokenizer.bos_token_id,
        early_stopping=True,
        max_time=timeout_s,
        stopping_criteria=stopping_criteria,
    )    
    time_taken = time.monotonic() - start_time    
    new_tokens = response[0][tokens["input_ids"].shape[1]:]
    print(f"generate took {time_taken} s to complete. tokens/s:{len(new_tokens)/time_taken}",flush=True)
    answer = tokenizer.decode(new_tokens, skip_special_tokens=True)
    return [(answer,"")]


def init_model(model_dir,infer_params:Dict[str,str]={},sys_conf:Dict[str,str]={}):
    longContextMode = infer_params.get("longContextMode","true") == "true"    
    if longContextMode:
        old_init = transformers.models.llama.modeling_llama.LlamaRotaryEmbedding.__init__
        def ntk_scaled_init(self, dim, max_position_embeddings=4096, base=10000, device=None):

            #The method is just these three lines
            max_position_embeddings = 16384
            a = 8 #Alpha value
            base = base * a ** (dim / (dim-2)) #Base change formula

            old_init(self, dim, max_position_embeddings, base, device)    
        
        transformers.models.llama.modeling_llama.LlamaRotaryEmbedding.__init__ = ntk_scaled_init

    pretrained_model_dir = os.path.join(model_dir,"pretrained_model")
    adaptor_model_dir = model_dir
    is_adaptor_model = os.path.exists(pretrained_model_dir)

    if not is_adaptor_model:        
        pretrained_model_dir = model_dir

    tokenizer = AutoTokenizer.from_pretrained(pretrained_model_dir,trust_remote_code=True)
    tokenizer.padding_side="right"
    tokenizer.pad_token_id=0
    tokenizer.bos_token_id = 1
    
    quatization = infer_params.get("quatization","false") == "true"

    print(f"longContextMode:{longContextMode} quatization:{quatization}",flush=True)

    if quatization:
        nf4_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=False,
            bnb_4bit_compute_dtype=torch.bfloat16,
        )
        model = AutoModelForCausalLM.from_pretrained(
            pretrained_model_dir,
            trust_remote_code=True,            
            device_map="auto",
            quantization_config=nf4_config,
        )

    else:
        model = AutoModelForCausalLM.from_pretrained(pretrained_model_dir,trust_remote_code=True,
                                                device_map='auto',                                                
                                                torch_dtype=torch.bfloat16                                                
                                                )
    if is_adaptor_model:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, adaptor_model_dir)

    model.eval()  
    if quatization:
        model = torch.compile(model)      
    import types
    model.stream_chat = types.MethodType(stream_chat, model)     
    return (model,tokenizer)

