from agent_swarm.extract import collusion_wiki, rubyhack, swarmtraces, transluce

EXTRACTORS = {
    collusion_wiki.SOURCE_ID: collusion_wiki.extract,
    transluce.SOURCE_ID: transluce.extract,
    swarmtraces.SOURCE_ID: swarmtraces.extract,
    rubyhack.SOURCE_ID: rubyhack.extract,
}
