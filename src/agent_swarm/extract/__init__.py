from agent_swarm.extract import collusion_wiki, swarmtraces, transluce

EXTRACTORS = {
    collusion_wiki.SOURCE_ID: collusion_wiki.extract,
    transluce.SOURCE_ID: transluce.extract,
    swarmtraces.SOURCE_ID: swarmtraces.extract,
}
