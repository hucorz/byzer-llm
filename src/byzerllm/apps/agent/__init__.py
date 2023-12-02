
from dataclasses import dataclass
from typing import TYPE_CHECKING,Dict, List, Optional, Union,Any
from .agent import Agent
from ray.util.client.common import ClientActorHandle, ClientObjectRef
import ray
import dataclasses
import copy

if TYPE_CHECKING:
    from .conversable_agent import ConversableAgent
    from byzerllm.utils.client import ByzerLLM,ByzerRetrieval

@dataclasses.dataclass
class ChatResponse:
      status: int
      output: str      
      code: str
      prompt: str
      variables: Dict[str,Any]=dataclasses.field(default_factory=dict)

def get_agent_name(agent: Union[Agent,ClientActorHandle,str]) -> str:
    if isinstance(agent,Agent):
        agent_name = agent.name
    elif isinstance(agent,str):
        agent_name = agent
    else:    
        agent_name = ray.get(agent.get_name.remote())

    return agent_name    

def run_agent_func(agent: Union[Agent,"ConversableAgent",ClientActorHandle], func_name: str, *args, **kwargs):
    """Run a function of an agent."""
    if isinstance(agent,Agent):
        return getattr(agent, func_name)(*args, **kwargs)
    elif isinstance(agent,str):
        return ray.get(getattr(ray.get_actor(agent), func_name).remote(*args, **kwargs))    
    else:
        return ray.get(getattr(agent, func_name).remote(*args, **kwargs)) 
    
def copy_message(message: Dict) -> Dict:
    return copy.deepcopy(message)

def modify_message_metadata(message: Dict, **kwargs) -> Dict:
    message = copy_message(message)
    if "metadata" not in message:
        message["metadata"] = {}
    for key, value in kwargs.items():
        message["metadata"][key] = value
    return message

def modify_message_content(message: Dict, content:str) -> Dict:
    message = copy_message(message)
    message["content"] = content
    return message

def modify_last_message(messages: List[Dict], message:Dict) -> List[Dict]:
    messages = copy_message(messages)
    messages[-1] = message
    return messages

# if tokenizer.__class__.__name__ == 'QWenTokenizer':
def qwen_chat(llm:"ByzerLLM",conversations: Optional[List[Dict]] = None,llm_config={}):
    
    
    for conv in conversations:
        if conv["role"] == "system":
            conv["content"] = "<|im_start|>system\n" + conv["content"] + "<|im_end|>"
            
    t = llm.chat_oai(conversations=conversations,role_mapping={
                    "user_role":"<|im_start|>user\n",
                    "assistant_role": "<|im_end|>\n<|im_start|>assistant\n",
                    "system_msg":"<|im_start|>system\nYou are a helpful assistant. Think it over and answer the user question correctly.<|im_end|>"
                    },  **{**{
                        "max_length":1024*16,
                        "top_p":0.95,
                        "temperature":0.01,
                    },**llm_config,**{
                        "generation.early_stopping":False,
                        "generation.repetition_penalty":1.1,
                        "generation.stop_token_ids":[151643]}})       
    v = t[0].output
    if "<|im_end|>" in v:
        v = v.split("<|im_end|>")[0]
    if "<|endoftext|>" in v:
        v = v.split("<|endoftext|>")[0]
    return v    

        

class Agents:
    @staticmethod
    def create_remote_agent(cls,name:str,llm,retrieval,*args, **kwargs)->ClientActorHandle:
        return ray.remote(name=name,max_concurrency=10)(cls).remote(
        name=name,llm=llm,retrieval=retrieval,*args, **kwargs) 

    @staticmethod
    def create_local_agent(cls,name:str,llm,retrieval,*args, **kwargs)->Agent:
        return cls(name=name,llm=llm,retrieval=retrieval,*args, **kwargs)

    @staticmethod
    def create_local_group(group_name:str,agents: List[Agent],llm,retrieval,*args, **kwargs) -> List[Agent]:
        from .groupchat import GroupChat
        from .groupchat import GroupChatManager

        if any([not isinstance(agent,Agent) for agent in agents]):
            raise ValueError("agents must be a list of Agent objects")
                
        group_parameters = ["messages","max_round","admin_name","func_call_filter"]
        group_parameters_dict = {}
        for parameter in group_parameters:
            if parameter in kwargs:
                group_parameters_dict[parameter] = kwargs[parameter]
                del kwargs[parameter]

        groupchat = GroupChat(agents=agents, **group_parameters_dict)
        group_chat_manager =Agents.create_local_agent(GroupChatManager,name=group_name,
                                                       llm=llm,retrieval=retrieval,
                                                       groupchat=groupchat,*args, **kwargs)
        return group_chat_manager
    
    @staticmethod
    def create_remote_group(group_name:str,agents: List[Union[Agent,ClientActorHandle,str]],llm,retrieval,*args, **kwargs) -> List[Union[Agent,ClientActorHandle,str]]:
        from .groupchat import GroupChat
        from .groupchat import GroupChatManager
                
        group_parameters = ["messages","max_round","admin_name","func_call_filter"]
        group_parameters_dict = {}
        for parameter in group_parameters:
            if parameter in kwargs:
                group_parameters_dict[parameter] = kwargs[parameter]
                del kwargs[parameter]

        groupchat = GroupChat(agents=agents, **group_parameters_dict)
        group_chat_manager =Agents.create_remote_agent(GroupChatManager,name=group_name,
                                                       llm=llm,retrieval=retrieval,
                                                       groupchat=groupchat,*args, **kwargs)
        return group_chat_manager   
    


        